"""Natural language to SQL query execution endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from querymind.api.dependencies import get_query_service
from querymind.domain.models import QueryRequest, QueryResponse
from querymind.services.query_service import QueryService

router = APIRouter(prefix="/api/v1", tags=["query"])


@router.post("/query", response_model=QueryResponse)
def run_query(
    payload: QueryRequest,
    service: Annotated[QueryService, Depends(get_query_service)],
) -> QueryResponse:
    """Translate plain English to validated read-only SQL, execute it, and explain it."""
    return service.ask(payload)
