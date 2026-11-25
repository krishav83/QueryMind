"""FastAPI dependency wiring for QueryMind services."""

from __future__ import annotations

from fastapi import Request

from querymind.config import Settings
from querymind.db.executor import QueryExecutor
from querymind.db.inspector import SchemaInspector
from querymind.db.manager import DatabaseManager
from querymind.explainer.explainer import SQLExplainer
from querymind.llm.factory import create_llm_provider
from querymind.security.validator import SQLValidator
from querymind.services.query_service import QueryService


def build_query_service(settings: Settings) -> QueryService:
    """Assemble a ``QueryService`` wired from runtime ``Settings``."""
    db_path = settings.resolved_db_path
    inspector = SchemaInspector(db_path)
    provider = create_llm_provider(settings)
    validator = SQLValidator(default_max_rows=settings.max_rows)
    executor = QueryExecutor(
        db_path,
        max_rows=settings.max_rows,
        timeout_seconds=settings.query_timeout_seconds,
    )
    explainer = SQLExplainer()
    return QueryService(
        inspector=inspector,
        provider=provider,
        validator=validator,
        executor=executor,
        explainer=explainer,
    )


def build_database_manager(settings: Settings, query_service: QueryService) -> DatabaseManager:
    """Assemble a ``DatabaseManager`` using controlled upload storage."""
    return DatabaseManager(
        upload_dir=settings.upload_dir,
        query_service=query_service,
        max_upload_size_mb=settings.max_upload_size_mb,
        max_upload_files_per_batch=settings.max_upload_files_per_batch,
        max_total_upload_batch_mb=settings.max_total_upload_batch_mb,
        initial_db_path=settings.resolved_db_path,
    )


def get_settings_dep(request: Request) -> Settings:
    """Retrieve runtime ``Settings`` from FastAPI app state."""
    return request.app.state.settings


def get_query_service(request: Request) -> QueryService:
    """Retrieve the active ``QueryService`` from FastAPI app state."""
    return request.app.state.query_service


def get_database_manager(request: Request) -> DatabaseManager:
    """Retrieve the active ``DatabaseManager`` from FastAPI app state."""
    return request.app.state.database_manager
