"""SQLite database upload, switching, listing, and disconnect endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field

from querymind.api.dependencies import get_database_manager
from querymind.db.manager import (
    BatchUploadResult,
    DatabaseManager,
    ManagedDatabase,
)
from querymind.domain.models import SchemaSnapshot

router = APIRouter(prefix="/api/v1/databases", tags=["databases"])


class DatabaseInfoResponse(BaseModel):
    """Sanitized database metadata returned to clients (no filesystem paths)."""

    id: str
    name: str
    size_bytes: int
    table_count: int
    status: str = "ready"


class DatabaseListResponse(BaseModel):
    """List of managed databases and current active selection."""

    active_id: str | None = None
    active_name: str | None = None
    databases: list[DatabaseInfoResponse] = Field(default_factory=list)


class DatabaseConnectResponse(BaseModel):
    """Response returned after uploading or switching to a SQLite database."""

    database: DatabaseInfoResponse
    schema_snapshot: SchemaSnapshot


class SelectDatabaseRequest(BaseModel):
    """Request body for switching to a previously uploaded database."""

    database_id: str = Field(min_length=1, max_length=64)


class DisconnectResponse(BaseModel):
    """Response returned after disconnecting the active database."""

    status: str = "disconnected"
    active_id: str | None = None
    active_name: str | None = None


class BatchUploadItemResponse(BaseModel):
    """Per-file validation and upload outcome within a batch upload."""

    filename: str
    status: str
    message: str
    database: DatabaseInfoResponse | None = None


class BatchUploadResponse(BaseModel):
    """Summary and per-file results for a multi-file SQLite upload batch."""

    total_files: int
    uploaded_count: int
    rejected_count: int
    results: list[BatchUploadItemResponse]
    active_id: str | None = None
    active_name: str | None = None


def _to_info(entry: ManagedDatabase) -> DatabaseInfoResponse:
    return DatabaseInfoResponse(
        id=entry.id,
        name=entry.name,
        size_bytes=entry.size_bytes,
        table_count=entry.table_count,
        status=entry.status,
    )


@router.get("", response_model=DatabaseListResponse)
def list_databases(
    manager: Annotated[DatabaseManager, Depends(get_database_manager)],
) -> DatabaseListResponse:
    """List uploaded databases available in this session without exposing file paths."""
    active = manager.active_database
    return DatabaseListResponse(
        active_id=active.id if active is not None else None,
        active_name=active.name if active is not None else None,
        databases=[_to_info(item) for item in manager.list_databases()],
    )


@router.post("/upload", response_model=DatabaseConnectResponse)
def upload_database(
    manager: Annotated[DatabaseManager, Depends(get_database_manager)],
    file: Annotated[UploadFile, File()],
) -> DatabaseConnectResponse:
    """Validate, store, connect, and inspect an uploaded SQLite database."""
    try:
        entry, snapshot = manager.upload_and_connect(file.filename or "", file.file)
    finally:
        file.file.close()
    return DatabaseConnectResponse(database=_to_info(entry), schema_snapshot=snapshot)


@router.post("/upload-batch", response_model=BatchUploadResponse)
def upload_databases_batch(
    manager: Annotated[DatabaseManager, Depends(get_database_manager)],
    files: Annotated[list[UploadFile], File()],
    auto_connect: Annotated[bool, Query()] = False,
) -> BatchUploadResponse:
    """Validate and store up to 15 SQLite databases independently in one operation."""
    file_pairs = [(f.filename or "", f.file) for f in files]
    try:
        batch_result: BatchUploadResult = manager.upload_batch(
            file_pairs,
            auto_connect_first=auto_connect,
        )
    finally:
        for f in files:
            f.file.close()

    active = manager.active_database
    return BatchUploadResponse(
        total_files=len(batch_result.results),
        uploaded_count=batch_result.uploaded_count,
        rejected_count=batch_result.rejected_count,
        results=[
            BatchUploadItemResponse(
                filename=item.filename,
                status=item.status,
                message=item.message,
                database=_to_info(item.database) if item.database is not None else None,
            )
            for item in batch_result.results
        ],
        active_id=active.id if active is not None else None,
        active_name=active.name if active is not None else None,
    )


@router.post("/select", response_model=DatabaseConnectResponse)
def select_database(
    payload: SelectDatabaseRequest,
    manager: Annotated[DatabaseManager, Depends(get_database_manager)],
) -> DatabaseConnectResponse:
    """Switch active connection to a previously uploaded database."""
    entry, snapshot = manager.select_database(payload.database_id)
    return DatabaseConnectResponse(database=_to_info(entry), schema_snapshot=snapshot)


@router.post("/disconnect", response_model=DisconnectResponse)
def disconnect_database(
    manager: Annotated[DatabaseManager, Depends(get_database_manager)],
) -> DisconnectResponse:
    """Disconnect the currently active database."""
    manager.disconnect()
    return DisconnectResponse()
