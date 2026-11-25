"""Controlled SQLite upload validation, storage, batch handling, and switching."""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from querymind.db.inspector import SchemaInspector
from querymind.domain.exceptions import (
    DatabaseConnectionError,
    DatabaseUploadError,
    DatabaseUploadTooLargeError,
)
from querymind.domain.models import SchemaSnapshot
from querymind.services.query_service import QueryService

ALLOWED_DB_EXTENSIONS: frozenset[str] = frozenset({".db", ".sqlite", ".sqlite3"})
_SQLITE_HEADER = b"SQLite format 3\x00"
_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ManagedDatabase:
    """Metadata for a validated SQLite database managed by QueryMind."""

    id: str
    name: str
    path: Path
    size_bytes: int
    table_count: int
    status: str = "ready"


@dataclass(frozen=True)
class UploadItemResult:
    """Per-file outcome returned when uploading a batch of SQLite files."""

    filename: str
    status: Literal["uploaded", "rejected"]
    message: str
    database: ManagedDatabase | None = None


@dataclass(frozen=True)
class BatchUploadResult:
    """Aggregate result for a multi-file SQLite upload operation."""

    results: tuple[UploadItemResult, ...]
    uploaded_count: int
    rejected_count: int


class DatabaseManager:
    """Validates, stores, switches, and disconnects user-uploaded SQLite databases."""

    def __init__(
        self,
        *,
        upload_dir: Path,
        query_service: QueryService,
        max_upload_size_mb: int = 25,
        max_upload_files_per_batch: int = 15,
        max_total_upload_batch_mb: int = 100,
        initial_db_path: Path | None = None,
    ) -> None:
        self._upload_dir = upload_dir.expanduser().resolve()
        self._query_service = query_service
        self._max_upload_bytes = max_upload_size_mb * 1024 * 1024
        self._max_batch_files = max_upload_files_per_batch
        self._max_batch_bytes = max_total_upload_batch_mb * 1024 * 1024
        self._lock = threading.RLock()
        self._databases: dict[str, ManagedDatabase] = {}
        self._active_id: str | None = None

        if initial_db_path is not None:
            resolved = initial_db_path.expanduser().resolve()
            if resolved.exists() and resolved.is_file():
                try:
                    schema = self._query_service.get_schema(force_refresh=True)
                    db_id = "configured"
                    self._databases[db_id] = ManagedDatabase(
                        id=db_id,
                        name=resolved.name,
                        path=resolved,
                        size_bytes=resolved.stat().st_size,
                        table_count=len(schema.tables),
                    )
                    self._active_id = db_id
                except Exception:
                    self._query_service.disconnect_database()

    @property
    def active_database(self) -> ManagedDatabase | None:
        """Return metadata for the currently connected database, if any."""
        with self._lock:
            if self._active_id is None:
                return None
            return self._databases.get(self._active_id)

    def list_databases(self) -> list[ManagedDatabase]:
        """Return registered databases in insertion order."""
        with self._lock:
            return list(self._databases.values())

    def upload_and_connect(
        self,
        filename: str,
        file_obj: BinaryIO,
    ) -> tuple[ManagedDatabase, SchemaSnapshot]:
        """Validate a single uploaded SQLite file, store it safely, and connect to it."""
        safe_name, ext = self._validate_filename(filename)
        payload = self._read_bounded_payload(file_obj)
        self._check_sqlite_header(payload)

        with self._lock:
            entry, snapshot = self._store_and_inspect(safe_name, ext, payload)
            try:
                connected_snapshot = self._query_service.connect_database(entry.path, entry.name)
            except Exception as exc:
                entry.path.unlink(missing_ok=True)
                raise DatabaseUploadError(
                    f"Could not connect to uploaded database: {exc}",
                    user_message="Could not connect to the uploaded SQLite database.",
                ) from exc

            final_entry = ManagedDatabase(
                id=entry.id,
                name=entry.name,
                path=entry.path,
                size_bytes=entry.size_bytes,
                table_count=len(connected_snapshot.tables),
                status="ready",
            )
            self._databases[final_entry.id] = final_entry
            self._active_id = final_entry.id
            return final_entry, snapshot

    def upload_batch(
        self,
        files: Sequence[tuple[str, BinaryIO]],
        *,
        auto_connect_first: bool = False,
    ) -> BatchUploadResult:
        """Validate and store a batch of SQLite databases independently."""
        if not files:
            raise DatabaseUploadError(
                "No files were provided in upload batch.",
                user_message="Please select at least one SQLite database file.",
            )

        if len(files) > self._max_batch_files:
            raise DatabaseUploadError(
                f"Upload batch contains {len(files)} files, exceeding limit of "
                f"{self._max_batch_files}.",
                user_message=(
                    f"Too many files selected ({len(files)}). "
                    f"Maximum allowed per upload batch is {self._max_batch_files}."
                ),
            )

        # Read and check total batch size limit before committing any uploaded files.
        staged_inputs: list[tuple[str, bytes | None, str | None]] = []
        total_batch_bytes = 0
        for raw_name, file_obj in files:
            try:
                payload = self._read_bounded_payload(file_obj)
                total_batch_bytes += len(payload)
                if total_batch_bytes > self._max_batch_bytes:
                    max_batch_mb = self._max_batch_bytes // (1024 * 1024)
                    raise DatabaseUploadTooLargeError(
                        f"Total upload batch size exceeds {max_batch_mb} MB limit.",
                        user_message=(
                            f"Total upload batch size exceeds the {max_batch_mb} MB limit."
                        ),
                    )
                staged_inputs.append((raw_name, payload, None))
            except DatabaseUploadTooLargeError as exc:
                # Re-raise immediately when the overall batch limit is exceeded.
                if total_batch_bytes > self._max_batch_bytes:
                    raise
                staged_inputs.append((raw_name, None, exc.user_message))
            except DatabaseUploadError as exc:
                staged_inputs.append((raw_name, None, exc.user_message))

        results: list[UploadItemResult] = []
        uploaded_entries: list[ManagedDatabase] = []

        with self._lock:
            for raw_name, payload, read_error in staged_inputs:
                display_label = self._safe_display_label(raw_name)
                if read_error is not None or payload is None:
                    results.append(
                        UploadItemResult(
                            filename=display_label,
                            status="rejected",
                            message=read_error or "Failed to read uploaded file.",
                        )
                    )
                    continue

                try:
                    safe_name, ext = self._validate_filename(raw_name)
                    self._check_sqlite_header(payload)
                    entry, _ = self._store_and_inspect(safe_name, ext, payload)
                    self._databases[entry.id] = entry
                    uploaded_entries.append(entry)
                    results.append(
                        UploadItemResult(
                            filename=entry.name,
                            status="uploaded",
                            message="Uploaded successfully.",
                            database=entry,
                        )
                    )
                except DatabaseUploadError as exc:
                    results.append(
                        UploadItemResult(
                            filename=display_label,
                            status="rejected",
                            message=exc.user_message,
                        )
                    )

            if auto_connect_first and self._active_id is None and uploaded_entries:
                first = uploaded_entries[0]
                self._query_service.connect_database(first.path, first.name)
                self._active_id = first.id

        return BatchUploadResult(
            results=tuple(results),
            uploaded_count=len(uploaded_entries),
            rejected_count=len(results) - len(uploaded_entries),
        )

    def select_database(self, database_id: str) -> tuple[ManagedDatabase, SchemaSnapshot]:
        """Switch active connection to a previously uploaded managed database."""
        with self._lock:
            entry = self._databases.get(database_id)
            if entry is None:
                raise DatabaseConnectionError(
                    f"Unknown database id '{database_id}'.",
                    user_message="Selected database could not be found.",
                )
            if not entry.path.exists():
                raise DatabaseConnectionError(
                    f"Managed database file missing for '{database_id}'.",
                    user_message="Selected database file is no longer available.",
                )
            snapshot = self._query_service.connect_database(entry.path, entry.name)
            self._active_id = database_id
            return entry, snapshot

    def disconnect(self) -> None:
        """Disconnect the currently active database without deleting uploaded files."""
        with self._lock:
            self._query_service.disconnect_database()
            self._active_id = None

    def _store_and_inspect(
        self,
        safe_name: str,
        ext: str,
        payload: bytes,
    ) -> tuple[ManagedDatabase, SchemaSnapshot]:
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        dest = self._allocate_managed_path(safe_name, ext)
        try:
            dest.write_bytes(payload)
            inspector = SchemaInspector(dest)
            snapshot = inspector.get_schema(force_refresh=True)
            if not snapshot.tables:
                raise DatabaseUploadError(
                    "Uploaded SQLite database contains zero user tables.",
                    user_message=(
                        "The uploaded SQLite database is empty (no tables found). "
                        "Please upload a database containing at least one table."
                    ),
                )
        except Exception as exc:
            if dest.exists():
                dest.unlink(missing_ok=True)
            if isinstance(exc, DatabaseUploadError):
                raise
            raise DatabaseUploadError(
                f"Uploaded file is not a readable SQLite database: {exc}",
                user_message=(
                    "Could not read tables from the uploaded SQLite file. "
                    "Ensure the file is a valid, uncorrupted SQLite database."
                ),
            ) from exc

        entry = ManagedDatabase(
            id=uuid.uuid4().hex[:12],
            name=safe_name,
            path=dest,
            size_bytes=len(payload),
            table_count=len(snapshot.tables),
            status="ready",
        )
        return entry, snapshot

    @staticmethod
    def _check_sqlite_header(payload: bytes) -> None:
        if len(payload) < len(_SQLITE_HEADER) or payload[: len(_SQLITE_HEADER)] != _SQLITE_HEADER:
            raise DatabaseUploadError(
                "Uploaded file does not have a valid SQLite 3 header.",
                user_message="Invalid SQLite file. Please upload a valid SQLite 3 database.",
            )

    @staticmethod
    def _safe_display_label(filename: str) -> str:
        raw = Path((filename or "").replace("\x00", "")).name.strip()
        return raw[:100] if raw else "unnamed_file"

    @staticmethod
    def _validate_filename(filename: str) -> tuple[str, str]:
        raw = (filename or "").strip()
        if not raw or "\x00" in raw or ".." in raw or "/" in raw or "\\" in raw or ":" in raw:
            raise DatabaseUploadError(
                f"Unsafe database filename rejected: {filename!r}",
                user_message="Invalid database filename.",
            )

        path_obj = Path(raw)
        ext = path_obj.suffix.lower()
        if ext not in ALLOWED_DB_EXTENSIONS:
            raise DatabaseUploadError(
                f"Unsupported database file extension: {ext!r}",
                user_message=(
                    "Unsupported file type. Please upload a .db, .sqlite, or .sqlite3 file."
                ),
            )

        stem = _SAFE_STEM_RE.sub("_", path_obj.stem).strip("._-") or "database"
        safe_display = f"{stem[:80]}{ext}"
        return safe_display, ext

    def _read_bounded_payload(self, file_obj: BinaryIO) -> bytes:
        chunks: list[bytes] = []
        total = 0
        chunk_size = 256 * 1024
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > self._max_upload_bytes:
                max_mb = self._max_upload_bytes // (1024 * 1024)
                raise DatabaseUploadTooLargeError(
                    f"Uploaded database exceeds {max_mb} MB limit.",
                    user_message=f"Uploaded file exceeds the {max_mb} MB size limit.",
                )
            chunks.append(chunk)

        if total == 0:
            raise DatabaseUploadError(
                "Uploaded database file is empty (0 bytes).",
                user_message="Uploaded file is empty. Please upload a valid SQLite database.",
            )
        return b"".join(chunks)

    def _allocate_managed_path(self, safe_name: str, ext: str) -> Path:
        stem = Path(safe_name).stem
        for _ in range(10):
            candidate = (self._upload_dir / f"{stem}_{uuid.uuid4().hex[:8]}{ext}").resolve()
            if not candidate.is_relative_to(self._upload_dir):
                raise DatabaseUploadError(
                    "Resolved upload path escaped managed directory.",
                    user_message="Invalid upload destination.",
                )
            if not candidate.exists():
                return candidate
        raise DatabaseUploadError(
            "Unable to allocate unique upload filename.",
            user_message="Unable to store uploaded database safely.",
        )
