"""Unit tests for dual-engine SQLite isolation and default-deny authorizers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from querymind.db.engine import (
    create_execution_engine,
    create_inspection_engine,
    make_execution_authorizer,
    make_inspection_authorizer,
)
from querymind.domain.exceptions import DatabaseConnectionError


def test_missing_database_raises_connection_error_without_creating_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.db"
    with pytest.raises(DatabaseConnectionError):
        create_inspection_engine(missing)
    with pytest.raises(DatabaseConnectionError):
        create_execution_engine(missing)
    assert not missing.exists()


def test_authorizers_default_deny_unknown_or_unhandled_actions() -> None:
    insp_auth = make_inspection_authorizer()
    exec_auth = make_execution_authorizer(allowed_tables={"customers"})

    # Unknown action codes must be denied by default.
    assert insp_auth(999999, "customers", None, "main", None) == sqlite3.SQLITE_DENY
    assert exec_auth(999999, "customers", None, "main", None) == sqlite3.SQLITE_DENY

    # Empty table name in SQLITE_READ must be denied.
    assert insp_auth(sqlite3.SQLITE_READ, "", None, "main", None) == sqlite3.SQLITE_DENY
    assert exec_auth(sqlite3.SQLITE_READ, "", None, "main", None) == sqlite3.SQLITE_DENY

    # Execution authorizer respects allowed_tables allowlist.
    assert exec_auth(sqlite3.SQLITE_READ, "customers", "name", "main", None) == sqlite3.SQLITE_OK
    assert exec_auth(sqlite3.SQLITE_READ, "orders", "id", "main", None) == sqlite3.SQLITE_DENY


def test_inspection_engine_allows_metadata_and_denies_mutations(seeded_db_path: Path) -> None:
    engine = create_inspection_engine(seeded_db_path)
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name")
            ).fetchall()
            assert [r[0] for r in tables] == ["customers", "order_items", "orders", "products"]

            pragma_cols = conn.execute(text("PRAGMA table_info('customers')")).fetchall()
            assert len(pragma_cols) == 6

            with pytest.raises(DatabaseError):
                conn.execute(text("PRAGMA query_only = OFF"))

            with pytest.raises(DatabaseError):
                conn.execute(text("DELETE FROM customers"))
    finally:
        engine.dispose()


def test_execution_engine_allows_analytical_select_and_denies_system_tables(
    seeded_db_path: Path,
) -> None:
    engine = create_execution_engine(seeded_db_path, allowed_tables={"orders"})
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*), ROUND(AVG(total_amount), 2) FROM orders")
            ).fetchone()
            assert row is not None
            assert row[0] == 6

            with pytest.raises(DatabaseError):
                conn.execute(text("SELECT name FROM sqlite_schema"))

            with pytest.raises(DatabaseError):
                conn.execute(text("SELECT id FROM customers"))
    finally:
        engine.dispose()
