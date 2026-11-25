"""Security tests verifying SQLite driver-level read-only and authorizer defenses."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from querymind.db.engine import create_execution_engine, create_inspection_engine


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "INSERT INTO customers (id, name, email, country, segment, created_at) "
        "VALUES (99, 'x', 'x@x.com', 'US', 'SMB', '2025-01-01')",
        "UPDATE customers SET name = 'hacked' WHERE id = 1",
        "DELETE FROM orders WHERE id = 1",
        "CREATE TABLE evil (x TEXT)",
        "DROP TABLE orders",
        "ALTER TABLE customers ADD COLUMN pwn TEXT",
        "ATTACH DATABASE ':memory:' AS extra",
        "DETACH DATABASE main",
        "VACUUM",
        "REINDEX",
        "ANALYZE",
        "PRAGMA table_info('customers')",
        "PRAGMA query_only = OFF",
        "PRAGMA writable_schema = ON",
        "SELECT name FROM sqlite_master",
        "SELECT name FROM sqlite_schema",
        "SELECT sqlite_version()",
        "SELECT randomblob(1000000)",
        "SELECT zeroblob(1000000)",
        "SELECT load_extension('evil.dll')",
    ],
)
def test_execution_engine_blocks_all_writes_ddl_pragmas_system_tables_and_unsafe_funcs(
    seeded_db_path,
    malicious_sql: str,
) -> None:
    engine = create_execution_engine(
        seeded_db_path,
        allowed_tables={"customers", "products", "orders", "order_items"},
    )
    try:
        with engine.connect() as conn:
            with pytest.raises(SQLAlchemyError):
                conn.execute(text(malicious_sql))
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "malicious_inspection_sql",
    [
        "INSERT INTO products (id, name, category, unit_price, in_stock) "
        "VALUES (99, 'x', 'x', 1.0, 1)",
        "DROP TABLE products",
        "ATTACH DATABASE ':memory:' AS extra",
        "PRAGMA query_only = OFF",
        "PRAGMA writable_schema = ON",
        "SELECT randomblob(1000)",
    ],
)
def test_inspection_engine_also_blocks_mutations_attach_and_unsafe_pragmas(
    seeded_db_path,
    malicious_inspection_sql: str,
) -> None:
    engine = create_inspection_engine(seeded_db_path)
    try:
        with engine.connect() as conn:
            with pytest.raises(SQLAlchemyError):
                conn.execute(text(malicious_inspection_sql))
    finally:
        engine.dispose()
