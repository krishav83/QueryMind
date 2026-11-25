"""Health check endpoint (exposes provider and connection status, never secrets or paths)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from querymind.api.dependencies import (
    get_database_manager,
    get_query_service,
    get_settings_dep,
)
from querymind.config import Settings
from querymind.db.manager import DatabaseManager
from querymind.llm.factory import create_llm_provider
from querymind.services.query_service import QueryService

router = APIRouter(prefix="/api/v1", tags=["health"])


class HealthResponse(BaseModel):
    """Non-sensitive service health status."""

    status: str
    app_env: str
    provider_mode: str
    provider_name: str
    database_ready: bool
    database_name: str | None = None


@router.get("/health", response_model=HealthResponse)
def health_check(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[QueryService, Depends(get_query_service)],
    manager: Annotated[DatabaseManager, Depends(get_database_manager)],
) -> HealthResponse:
    """Report application readiness and active database/provider status without secrets."""
    provider = create_llm_provider(settings)
    db_ready = False
    db_name: str | None = None
    if service.is_connected:
        try:
            service.get_schema()
            db_ready = True
            active = manager.active_database
            db_name = active.name if active is not None else service.database_name
        except Exception:
            db_ready = False

    return HealthResponse(
        status="ok",
        app_env=settings.app_env,
        provider_mode=provider.mode,
        provider_name=provider.name,
        database_ready=db_ready,
        database_name=db_name,
    )
