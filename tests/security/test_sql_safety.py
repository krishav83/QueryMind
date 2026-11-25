"""Adversarial security tests for SQLGlot AST validator."""

from __future__ import annotations

import pytest

from querymind.domain.exceptions import SQLValidationError, UnknownTableError, UnsafeSQLError
from querymind.domain.models import ColumnInfo, SchemaSnapshot, TableInfo
from querymind.security.validator import SQLValidator


@pytest.fixture()
def schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        dialect="sqlite",
        tables=(
            TableInfo(
                name="orders",
                columns=(
                    ColumnInfo(name="id", data_type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="status", data_type="TEXT", nullable=False),
                    ColumnInfo(name="total_amount", data_type="REAL", nullable=False),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    "adversarial_sql",
    [
        # Stacked / multi-statement attacks
        "SELECT id FROM orders; DROP TABLE orders;",
        "SELECT id FROM orders; INSERT INTO orders VALUES (9, 'x', 1.0);",
        # DML / DDL / PRAGMA / ATTACH
        "INSERT INTO orders (id, status, total_amount) VALUES (99, 'ok', 1.0)",
        "UPDATE orders SET status = 'hacked'",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN hack TEXT",
        "CREATE TABLE evil (a INT)",
        "PRAGMA table_info('orders')",
        "PRAGMA query_only = OFF",
        "ATTACH DATABASE ':memory:' AS mem",
        # Comment-obfuscated mutations
        "/* comment */ DELETE FROM orders",
        # System catalog probing
        "SELECT name FROM sqlite_master",
        "SELECT name FROM sqlite_schema",
        "SELECT sql FROM sqlite_temp_master",
        "SELECT * FROM pragma_table_info('orders')",
        # Dangerous functions
        "SELECT load_extension('evil') FROM orders",
        "SELECT randomblob(100000000) FROM orders",
        "SELECT zeroblob(100000000) FROM orders",
        "SELECT sqlite_version() FROM orders",
        "SELECT readfile('/etc/passwd') FROM orders",
        # Recursive CTE DoS
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt) SELECT x FROM cnt",
        # Non-SELECT CTE / subquery bypasses
        "SELECT * FROM (SELECT sqlite_version() AS v) sub",
        # Invalid LIMIT expressions
        "SELECT id FROM orders LIMIT 0",
        "SELECT id FROM orders LIMIT -5",
        "SELECT id FROM orders LIMIT (SELECT 100)",
        # Disallowed bind styles & hex literals
        "SELECT id FROM orders WHERE status = ? LIMIT 5",
        "SELECT id FROM orders WHERE status = @status LIMIT 5",
        "SELECT id FROM orders WHERE status = X'4142' LIMIT 5",
    ],
)
def test_validator_blocks_adversarial_sql_vectors(
    schema: SchemaSnapshot,
    adversarial_sql: str,
) -> None:
    validator = SQLValidator()
    with pytest.raises((UnsafeSQLError, SQLValidationError)):
        validator.validate(adversarial_sql, schema)


def test_validator_blocks_unknown_tables_and_injection_string_literals_are_parameterized(
    schema: SchemaSnapshot,
) -> None:
    validator = SQLValidator()
    with pytest.raises(UnknownTableError):
        validator.validate("SELECT * FROM users", schema)

    # An SQL injection payload inside a string literal must be bound safely as a parameter.
    validated = validator.validate(
        "SELECT id FROM orders WHERE status = 'completed'' OR 1=1 --'",
        schema,
    )
    assert "OR 1=1" not in validated.sql
    assert validated.params["p_0"] == "completed' OR 1=1 --"
