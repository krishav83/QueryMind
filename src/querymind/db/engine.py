"""Dual SQLite engine factories with strict default-deny authorizer isolation."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import NullPool

from querymind.domain.exceptions import DatabaseConnectionError

# Standard SQLite analytical, aggregate, scalar, date/time, and conditional functions
# permitted for validated read-only analytical queries.
ALLOWED_EXECUTION_FUNCTIONS: frozenset[str] = frozenset(
    {
        # Aggregates
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "total",
        "group_concat",
        # String functions
        "like",
        "glob",
        "lower",
        "upper",
        "length",
        "substr",
        "substring",
        "trim",
        "ltrim",
        "rtrim",
        "replace",
        "instr",
        "printf",
        "unicode",
        "hex",
        "quote",
        # Numeric / math functions
        "abs",
        "round",
        "sign",
        "mod",
        "ceil",
        "ceiling",
        "floor",
        "trunc",
        "pow",
        "power",
        "sqrt",
        "exp",
        "ln",
        "log",
        "log10",
        "log2",
        # Conditional & type helpers
        "coalesce",
        "ifnull",
        "nullif",
        "iif",
        "typeof",
        "cast",
        "likely",
        "unlikely",
        "likelihood",
        # Date / time functions
        "date",
        "time",
        "datetime",
        "julianday",
        "unixepoch",
        "strftime",
        "timediff",
    }
)

# Inspection connections need the same safe functions (plus sqlite_version for SQLAlchemy).
ALLOWED_INSPECTION_FUNCTIONS: frozenset[str] = ALLOWED_EXECUTION_FUNCTIONS | {"sqlite_version"}

# Exact read-only introspection PRAGMAs used by SQLAlchemy's SQLite dialect.
ALLOWED_INSPECTION_PRAGMAS: frozenset[str] = frozenset(
    {
        "table_info",
        "table_xinfo",
        "foreign_key_list",
        "index_list",
        "index_info",
        "database_list",
        "query_only",
    }
)

_INSPECTION_SYSTEM_TABLES: frozenset[str] = frozenset(
    {
        "sqlite_schema",
        "sqlite_master",
        "sqlite_temp_schema",
        "sqlite_temp_master",
        "sqlite_stat1",
        "sqlite_stat4",
    }
)

AuthorizerFn = Callable[[int, str | None, str | None, str | None, str | None], int]


def _validate_db_file(db_path: Path) -> Path:
    resolved = db_path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise DatabaseConnectionError(
            f"SQLite database file not found at {resolved}",
            user_message="Configured database file could not be found.",
        )
    return resolved


def make_inspection_authorizer() -> AuthorizerFn:
    """Create a default-deny SQLite authorizer allowing read-only schema introspection."""

    def _authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        db_name: str | None,
        trigger_name: str | None,
    ) -> int:
        del db_name, trigger_name

        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if not table:
                return sqlite3.SQLITE_DENY
            if table.startswith("sqlite_") and table not in _INSPECTION_SYSTEM_TABLES:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_PRAGMA:
            pragma_name = (arg1 or "").lower()
            if pragma_name not in ALLOWED_INSPECTION_PRAGMAS:
                return sqlite3.SQLITE_DENY
            if pragma_name == "query_only" and arg2 is not None:
                if arg2.strip().upper() not in {"1", "ON", "TRUE"}:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_FUNCTION:
            func_name = (arg2 or "").lower()
            if func_name in ALLOWED_INSPECTION_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        return sqlite3.SQLITE_DENY

    return _authorizer


def make_execution_authorizer(
    allowed_tables: Iterable[str] | None = None,
) -> AuthorizerFn:
    """Create a strict default-deny SQLite authorizer for query execution connections."""
    normalized_tables: frozenset[str] | None = (
        frozenset(t.lower() for t in allowed_tables) if allowed_tables is not None else None
    )

    def _authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        db_name: str | None,
        trigger_name: str | None,
    ) -> int:
        del db_name, trigger_name

        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if not table or table.startswith("sqlite_") or table.startswith("pragma_"):
                return sqlite3.SQLITE_DENY
            if normalized_tables is not None and table not in normalized_tables:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_FUNCTION:
            func_name = (arg2 or "").lower()
            if func_name in ALLOWED_EXECUTION_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        return sqlite3.SQLITE_DENY

    return _authorizer


def _create_sqlite_engine(db_path: Path, authorizer: AuthorizerFn) -> Engine:
    resolved = _validate_db_file(db_path)
    ro_uri = f"{resolved.as_uri()}?mode=ro"

    def _connect() -> sqlite3.Connection:
        conn = sqlite3.connect(ro_uri, uri=True, check_same_thread=False)
        try:
            conn.execute("PRAGMA query_only = ON;")
        except Exception:
            conn.close()
            raise
        return conn

    engine = create_engine(
        "sqlite://",
        creator=_connect,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )

    # Attach the strict authorizer on pool checkout (after SQLAlchemy's one-time
    # first_connect dialect initialization, which queries PRAGMA read_uncommitted).
    @event.listens_for(engine, "checkout")
    def _attach_authorizer(
        dbapi_connection: object,
        _conn_record: object,
        _conn_proxy: object,
    ) -> None:
        if isinstance(dbapi_connection, sqlite3.Connection):
            dbapi_connection.set_authorizer(authorizer)

    return engine


def create_inspection_engine(db_path: Path) -> Engine:
    """Build a read-only SQLAlchemy engine for trusted SQLAlchemy schema inspection."""
    return _create_sqlite_engine(db_path, make_inspection_authorizer())


def create_execution_engine(
    db_path: Path,
    allowed_tables: Iterable[str] | None = None,
) -> Engine:
    """Build a restricted read-only SQLAlchemy engine for validated query execution."""
    return _create_sqlite_engine(db_path, make_execution_authorizer(allowed_tables))
