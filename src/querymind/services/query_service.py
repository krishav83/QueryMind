"""End-to-end NL2SQL query orchestration enforcing validation before execution."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from querymind.db.executor import QueryExecutor
from querymind.db.inspector import SchemaInspector
from querymind.domain.models import QueryRequest, QueryResponse, SchemaSnapshot
from querymind.explainer.explainer import SQLExplainer
from querymind.llm.base import BaseLLMProvider
from querymind.security.validator import SQLValidator

_logger = logging.getLogger("querymind.services")


class QueryService:
    """Coordinates schema inspection, SQL generation, AST validation, execution, and explanation."""

    def __init__(
        self,
        *,
        inspector: SchemaInspector,
        provider: BaseLLMProvider,
        validator: SQLValidator,
        executor: QueryExecutor,
        explainer: SQLExplainer,
        database_name: str | None = None,
    ) -> None:
        self._inspector = inspector
        self._provider = provider
        self._validator = validator
        self._executor = executor
        self._explainer = explainer
        self._database_name = database_name or (
            inspector.db_path.name if inspector.db_path is not None else None
        )
        self._lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        """Return True when a database path or engine is currently attached."""
        with self._lock:
            return self._inspector.db_path is not None or self._inspector._engine is not None

    @property
    def database_name(self) -> str | None:
        """Return the display name of the currently connected database, if any."""
        with self._lock:
            return self._database_name

    def connect_database(self, db_path: Path, display_name: str) -> SchemaSnapshot:
        """Atomically validate and switch to ``db_path``, preserving prior DB on error."""
        with self._lock:
            prev_inspector_path = self._inspector.db_path
            prev_executor_path = self._executor.db_path
            prev_name = self._database_name
            try:
                self._inspector.set_database(db_path)
                snapshot = self._inspector.get_schema(force_refresh=True)
                self._executor.set_database(db_path)
                self._database_name = display_name
                return snapshot
            except Exception:
                self._inspector.set_database(prev_inspector_path)
                self._executor.set_database(prev_executor_path)
                self._database_name = prev_name
                if prev_inspector_path is not None:
                    try:
                        self._inspector.get_schema(force_refresh=True)
                    except Exception:
                        _logger.warning("Failed to restore schema cache for prior database.")
                raise

    def disconnect_database(self) -> None:
        """Disconnect the active database and dispose held engine/cache state."""
        with self._lock:
            self._inspector.set_database(None)
            self._executor.set_database(None)
            self._database_name = None

    def get_schema(self, *, force_refresh: bool = False) -> SchemaSnapshot:
        """Discover or return the cached database schema."""
        with self._lock:
            return self._inspector.get_schema(force_refresh=force_refresh)

    def ask(self, request: QueryRequest) -> QueryResponse:
        """Convert plain English to validated read-only SQL, execute it, and explain it."""
        with self._lock:
            schema = self._inspector.get_schema()
            generated = self._provider.generate_sql(request.question, schema)

            validated = self._validator.validate(
                generated.sql,
                schema,
                existing_params=generated.params,
                max_rows=request.max_rows,
            )

            execution = self._executor.execute(
                validated,
                max_rows=request.max_rows,
                allowed_tables=schema.table_names(),
            )

            if generated.mode == "llm" and generated.raw_explanation:
                explanation = generated.raw_explanation
            else:
                explanation = self._explainer.explain_from_ast(validated.sql, schema)

            return QueryResponse(
                question=request.question,
                sql=validated.sql,
                explanation=explanation,
                provider_mode=generated.mode,
                provider_name=generated.provider_name,
                columns=execution.columns,
                rows=execution.rows,
                row_count=execution.row_count,
                truncated=execution.truncated,
                execution_time_ms=execution.execution_time_ms,
            )
