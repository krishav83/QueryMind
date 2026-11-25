"""Unit tests for SQLGlot AST validation, scoping, LIMITs, and literal parameterization."""

from __future__ import annotations

import pytest

from querymind.domain.exceptions import (
    AmbiguousColumnError,
    UnknownColumnError,
    UnknownTableError,
    UnsafeSQLError,
    UnsupportedSQLError,
)
from querymind.domain.models import ColumnInfo, ForeignKeyInfo, SchemaSnapshot, TableInfo
from querymind.security.validator import SQLValidator


@pytest.fixture()
def sample_schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        dialect="sqlite",
        tables=(
            TableInfo(
                name="customers",
                columns=(
                    ColumnInfo(name="id", data_type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="name", data_type="TEXT", nullable=False),
                    ColumnInfo(name="country", data_type="TEXT", nullable=False),
                    ColumnInfo(name="segment", data_type="TEXT", nullable=False),
                ),
            ),
            TableInfo(
                name="orders",
                columns=(
                    ColumnInfo(name="id", data_type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="customer_id", data_type="INTEGER", nullable=False),
                    ColumnInfo(name="status", data_type="TEXT", nullable=False),
                    ColumnInfo(name="total_amount", data_type="REAL", nullable=False),
                ),
                foreign_keys=(
                    ForeignKeyInfo(
                        column="customer_id",
                        referred_table="customers",
                        referred_column="id",
                    ),
                ),
            ),
        ),
    )


def test_validator_injects_and_clamps_limit_and_parameterizes_literals(
    sample_schema: SchemaSnapshot,
) -> None:
    validator = SQLValidator(default_max_rows=50)

    # Missing LIMIT -> injected and string/numeric literals extracted into params
    q1 = validator.validate(
        "SELECT id, total_amount FROM orders WHERE status = 'completed' AND total_amount > 100",
        sample_schema,
    )
    assert q1.applied_limit == 50
    assert "LIMIT 50" in q1.sql
    assert "'completed'" not in q1.sql
    assert q1.params["p_0"] == "completed"
    assert q1.params["p_1"] == 100
    assert q1.referenced_tables == ("orders",)

    # Excessive LIMIT -> clamped
    q2 = validator.validate("SELECT id FROM orders LIMIT 9999", sample_schema)
    assert q2.applied_limit == 50
    assert "LIMIT 50" in q2.sql


def test_validator_handles_joins_and_rejects_ambiguous_or_wrong_alias_columns(
    sample_schema: SchemaSnapshot,
) -> None:
    validator = SQLValidator()

    valid = validator.validate(
        "SELECT c.name, o.total_amount FROM customers c "
        "JOIN orders o ON o.customer_id = c.id ORDER BY o.total_amount DESC",
        sample_schema,
    )
    assert valid.referenced_tables == ("customers", "orders")

    # Ambiguous unqualified column present in both customers and orders
    with pytest.raises(AmbiguousColumnError):
        validator.validate(
            "SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id",
            sample_schema,
        )

    # Column referenced under wrong alias
    with pytest.raises(UnknownColumnError):
        validator.validate(
            "SELECT c.total_amount FROM customers c JOIN orders o ON o.customer_id = c.id",
            sample_schema,
        )


def test_validator_enforces_cte_and_nested_subquery_column_projections(
    sample_schema: SchemaSnapshot,
) -> None:
    validator = SQLValidator()

    ok = validator.validate(
        "WITH big_orders AS (SELECT id, total_amount FROM orders WHERE total_amount > 200) "
        "SELECT big_orders.id, big_orders.total_amount FROM big_orders",
        sample_schema,
    )
    assert ok.referenced_tables == ("orders",)

    # CTE did not project 'status', even though orders has 'status'
    with pytest.raises(UnknownColumnError):
        validator.validate(
            "WITH big_orders AS (SELECT id, total_amount FROM orders) "
            "SELECT big_orders.status FROM big_orders",
            sample_schema,
        )

    # Derived subquery projection check
    ok_sub = validator.validate(
        "SELECT s.total_amount FROM (SELECT o.total_amount FROM orders o) s",
        sample_schema,
    )
    assert ok_sub.referenced_tables == ("orders",)

    with pytest.raises(UnknownColumnError):
        validator.validate(
            "SELECT s.status FROM (SELECT o.total_amount FROM orders o) s",
            sample_schema,
        )

    with pytest.raises(UnknownTableError):
        validator.validate(
            "SELECT other.total_amount FROM (SELECT o.total_amount FROM orders o) s",
            sample_schema,
        )


def test_validator_enforces_sqlite_select_alias_scoping_rules(
    sample_schema: SchemaSnapshot,
) -> None:
    validator = SQLValidator()

    # Valid SELECT alias in GROUP BY, HAVING, and ORDER BY
    ok = validator.validate(
        "SELECT status AS order_status, COUNT(*) AS cnt "
        "FROM orders GROUP BY order_status HAVING cnt > 1 ORDER BY cnt DESC",
        sample_schema,
    )
    assert ok.referenced_tables == ("orders",)

    # SELECT alias must NOT be accepted in WHERE
    with pytest.raises(UnknownColumnError):
        validator.validate(
            "SELECT total_amount AS amt FROM orders WHERE amt > 100",
            sample_schema,
        )

    # Ambiguous duplicate SELECT alias in ORDER BY
    with pytest.raises(AmbiguousColumnError):
        validator.validate(
            "SELECT status AS dup, customer_id AS dup FROM orders ORDER BY dup",
            sample_schema,
        )

    # Unresolved alias in ORDER BY
    with pytest.raises(UnknownColumnError):
        validator.validate(
            "SELECT status FROM orders ORDER BY nonexistent_alias",
            sample_schema,
        )


def test_validator_rejects_excessive_nesting_and_malformed_sql(
    sample_schema: SchemaSnapshot,
) -> None:
    validator = SQLValidator(max_nesting_depth=1)
    with pytest.raises(UnsupportedSQLError):
        validator.validate(
            "SELECT id FROM (SELECT id FROM (SELECT id FROM orders) a) b",
            sample_schema,
        )

    with pytest.raises(UnsupportedSQLError):
        validator.validate("SELECT FROM WHERE", sample_schema)

    with pytest.raises(UnsafeSQLError):
        validator.validate("   ", sample_schema)
