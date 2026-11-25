"""Read-only, timeout-protected query execution returning Pandas-normalized results."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from querymind.db.engine import create_execution_engine
from querymind.domain.exceptions import (
    DatabaseConnectionError,
    NoDatabaseConnectedError,
    QueryExecutionError,
    QueryTimeoutError,
)
from querymind.domain.models import ExecutionResult, ValidatedQuery


class QueryExecutor:
    """Executes only ``ValidatedQuery`` instances against a restricted read-only engine."""

    def __init__(
        self,
        db_path: Path | None,
        *,
        max_rows: int = 500,
        timeout_seconds: float = 5.0,
        allowed_tables: Iterable[str] | None = None,
        engine: Engine | None = None,
    ) -> None:
        self._db_path = db_path
        self._max_rows = max_rows
        self._timeout_seconds = timeout_seconds
        self._allowed_tables = tuple(allowed_tables) if allowed_tables is not None else None
        self._engine = engine

    @property
    def db_path(self) -> Path | None:
        """Return the active database path, if any."""
        return self._db_path

    def set_database(self, db_path: Path | None) -> None:
        """Switch or disconnect the execution database and dispose any held engine."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
        self._db_path = db_path
        self._allowed_tables = None

    def execute(
        self,
        query: ValidatedQuery,
        *,
        max_rows: int | None = None,
        allowed_tables: Iterable[str] | None = None,
    ) -> ExecutionResult:
        """Execute a validated read-only SQL query with bound parameters and timeout."""
        if not isinstance(query, ValidatedQuery):
            raise TypeError("QueryExecutor.execute requires a ValidatedQuery instance.")

        if self._engine is None and self._db_path is None:
            raise NoDatabaseConnectedError(
                "No database is currently connected.",
                user_message="No database connected. Please upload and connect a SQLite database.",
            )

        effective_tables = (
            tuple(allowed_tables) if allowed_tables is not None else self._allowed_tables
        )
        effective_limit = min(
            max_rows if max_rows is not None else self._max_rows,
            self._max_rows,
            query.applied_limit,
        )

        engine = self._engine or create_execution_engine(
            self._db_path,  # type: ignore[arg-type]
            allowed_tables=effective_tables,
        )
        started = time.monotonic()
        raw_conn: sqlite3.Connection | None = None

        try:
            with engine.connect() as sa_conn:
                dbapi_conn = sa_conn.connection.dbapi_connection
                if isinstance(dbapi_conn, sqlite3.Connection):
                    raw_conn = dbapi_conn
                    deadline = started + self._timeout_seconds

                    def _progress_handler() -> int:
                        return 1 if time.monotonic() > deadline else 0

                    raw_conn.set_progress_handler(_progress_handler, 1000)

                result = sa_conn.execute(text(query.sql), dict(query.params))
                columns = list(result.keys())
                fetched = result.fetchmany(effective_limit + 1)

            truncated = len(fetched) > effective_limit
            trimmed = fetched[:effective_limit]

            frame = pd.DataFrame([tuple(row) for row in trimmed], columns=columns)
            records = self._dataframe_to_records(frame)
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)

            return ExecutionResult(
                columns=columns,
                rows=records,
                row_count=len(records),
                truncated=truncated,
                execution_time_ms=elapsed_ms,
            )
        except DatabaseConnectionError:
            raise
        except OperationalError as exc:
            msg = str(exc).lower()
            if "interrupted" in msg:
                raise QueryTimeoutError(
                    f"Query exceeded timeout of {self._timeout_seconds}s: {exc}",
                    user_message=(
                        f"Query exceeded the {self._timeout_seconds:.1f}s execution time limit."
                    ),
                ) from exc
            raise QueryExecutionError(
                f"Query execution failed: {exc}",
                user_message="Query execution failed under read-only constraints.",
            ) from exc
        except SQLAlchemyError as exc:
            raise QueryExecutionError(
                f"Query execution failed: {exc}",
                user_message="Query execution failed under read-only constraints.",
            ) from exc
        finally:
            if raw_conn is not None:
                try:
                    raw_conn.set_progress_handler(None, 0)
                except sqlite3.Error:
                    pass
            if self._engine is None:
                engine.dispose()

    @staticmethod
    def _dataframe_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        """Convert Pandas DataFrame to JSON-safe Python dicts with None for missing values."""
        if frame.empty:
            return []
        cleaned = frame.astype(object).where(pd.notnull(frame), None)
        return cleaned.to_dict(orient="records")
