"""FastAPI application factory with sanitized error handlers."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from querymind import __version__
from querymind.api.dependencies import build_database_manager, build_query_service
from querymind.api.routes import databases, health, query, schema
from querymind.config import Settings, get_settings
from querymind.domain.exceptions import (
    ConfigurationError,
    DatabaseConnectionError,
    DatabaseUploadError,
    DatabaseUploadTooLargeError,
    LLMProviderError,
    NoDatabaseConnectedError,
    QueryExecutionError,
    QueryMindError,
    QueryTimeoutError,
    SchemaDiscoveryError,
    SQLValidationError,
    UnsupportedDeterministicQueryError,
)
from querymind.logging_config import configure_logging
from querymind.security.sanitizer import sanitize_error_message

_logger = logging.getLogger("querymind.api")


def _status_for_error(exc: QueryMindError) -> int:
    if isinstance(exc, (SQLValidationError, UnsupportedDeterministicQueryError)):
        return status.HTTP_400_BAD_REQUEST
    if isinstance(exc, DatabaseUploadTooLargeError):
        return 413
    if isinstance(exc, (DatabaseUploadError, NoDatabaseConnectedError)):
        return status.HTTP_400_BAD_REQUEST
    if isinstance(exc, QueryTimeoutError):
        return status.HTTP_504_GATEWAY_TIMEOUT
    if isinstance(exc, QueryExecutionError):
        return status.HTTP_400_BAD_REQUEST
    if isinstance(exc, LLMProviderError):
        return status.HTTP_502_BAD_GATEWAY
    if isinstance(exc, (DatabaseConnectionError, SchemaDiscoveryError, ConfigurationError)):
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the QueryMind FastAPI application."""
    active_settings = settings or get_settings()
    configure_logging(
        level=active_settings.log_level,
        secrets=active_settings.secret_values(),
    )

    app = FastAPI(
        title="QueryMind API",
        version=__version__,
        description="Safe AI-powered Natural Language to Read-Only SQL Assistant.",
    )
    app.state.settings = active_settings
    query_service = build_query_service(active_settings)
    app.state.query_service = query_service
    app.state.database_manager = build_database_manager(active_settings, query_service)

    @app.exception_handler(QueryMindError)
    async def _handle_domain_error(request: Request, exc: QueryMindError) -> JSONResponse:
        secrets = request.app.state.settings.secret_values()
        safe_detail = sanitize_error_message(exc.user_message, secrets=secrets)
        http_status = _status_for_error(exc)
        _logger.warning(
            "Handled domain error (%s): %s",
            exc.code,
            sanitize_error_message(exc, secrets),
        )
        return JSONResponse(
            status_code=http_status,
            content={"detail": safe_detail, "code": exc.code},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        secrets = request.app.state.settings.secret_values()
        _logger.error("Unhandled API exception: %s", sanitize_error_message(exc, secrets))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal server error occurred.", "code": "internal_error"},
        )

    app.include_router(health.router)
    app.include_router(databases.router)
    app.include_router(schema.router)
    app.include_router(query.router)
    return app


app = create_app()
