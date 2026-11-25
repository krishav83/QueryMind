"""Automatic database schema inspection endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from querymind.api.dependencies import get_query_service
from querymind.domain.models import SchemaSnapshot
from querymind.services.query_service import QueryService

router = APIRouter(prefix="/api/v1/schema", tags=["schema"])


@router.get("", response_model=SchemaSnapshot)
def get_schema(service: Annotated[QueryService, Depends(get_query_service)]) -> SchemaSnapshot:
    """Return the discovered database schema metadata."""
    return service.get_schema(force_refresh=False)


@router.post("/refresh", response_model=SchemaSnapshot)
def refresh_schema(
    service: Annotated[QueryService, Depends(get_query_service)],
) -> SchemaSnapshot:
    """Force re-inspection of the database schema."""
    return service.get_schema(force_refresh=True)
