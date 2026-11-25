"""Unit and security tests for DatabaseManager upload validation and lifecycle."""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from querymind.db.executor import QueryExecutor
from querymind.db.inspector import SchemaInspector
from querymind.db.manager import DatabaseManager
from querymind.domain.exceptions import (
    DatabaseConnectionError,
    DatabaseUploadError,
    DatabaseUploadTooLargeError,
)
from querymind.explainer.explainer import SQLExplainer
from querymind.llm.providers.fallback import DeterministicFallbackProvider
from querymind.security.validator import SQLValidator
from querymind.services.query_service import QueryService


def _create_sqlite_bytes(tmp_path: Path, filename: str, sql: str) -> bytes:
    db_file = tmp_path / filename
    with closing(sqlite3.connect(db_file)) as conn:
        if sql.strip():
            conn.executescript(sql)
        conn.commit()
    return db_file.read_bytes()


def _make_manager(
    tmp_path: Path, *, max_upload_mb: int = 5
) -> tuple[DatabaseManager, QueryService]:
    service = QueryService(
        inspector=SchemaInspector(None),
        provider=DeterministicFallbackProvider(),
        validator=SQLValidator(),
        executor=QueryExecutor(None),
        explainer=SQLExplainer(),
    )
    manager = DatabaseManager(
        upload_dir=tmp_path / "uploads",
        query_service=service,
        max_upload_size_mb=max_upload_mb,
    )
    return manager, service


@pytest.mark.parametrize("ext", [".db", ".sqlite", ".sqlite3"])
def test_upload_valid_multi_table_sqlite_and_prevent_overwrite(
    tmp_path: Path,
    ext: str,
) -> None:
    manager, service = _make_manager(tmp_path)
    raw = _create_sqlite_bytes(
        tmp_path,
        f"src{ext}",
        """
        CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            department_id INTEGER NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(id)
        );
        INSERT INTO departments VALUES (1, 'Engineering');
        INSERT INTO employees VALUES (1, 'Priya', 1);
        """,
    )

    entry1, schema1 = manager.upload_and_connect(f"company{ext}", io.BytesIO(raw))
    entry2, _ = manager.upload_and_connect(f"company{ext}", io.BytesIO(raw))

    assert service.is_connected is True
    assert entry1.name == f"company{ext}"
    assert entry1.table_count == 2
    assert schema1.table_names() == ("departments", "employees")
    # Uploading the same filename must allocate distinct paths without overwriting.
    assert entry1.path != entry2.path
    assert entry1.path.exists()
    assert entry2.path.exists()


@pytest.mark.parametrize(
    "bad_filename",
    [
        "",
        "../secret.db",
        "..\\secret.db",
        "subdir/secret.db",
        "subdir\\secret.db",
        "C:\\evil.db",
        "bad\x00.db",
        "notes.txt",
        "payload.exe",
        "archive.zip",
    ],
)
def test_upload_rejects_path_traversal_and_unsupported_extensions(
    tmp_path: Path,
    bad_filename: str,
) -> None:
    manager, _ = _make_manager(tmp_path)
    with pytest.raises(DatabaseUploadError):
        manager.upload_and_connect(bad_filename, io.BytesIO(b"SQLite format 3\x00dummy"))


def test_upload_rejects_empty_file_corrupt_header_and_empty_tables(tmp_path: Path) -> None:
    manager, service = _make_manager(tmp_path)

    with pytest.raises(DatabaseUploadError, match="empty"):
        manager.upload_and_connect("empty.db", io.BytesIO(b""))

    with pytest.raises(DatabaseUploadError, match="SQLite 3 header") as header_exc:
        manager.upload_and_connect("fake.db", io.BytesIO(b"not a real sqlite database"))
    assert "Invalid SQLite" in header_exc.value.user_message

    # Force SQLite to materialize an empty 0-table database file
    no_table_path = tmp_path / "materialized_empty.db"
    with closing(sqlite3.connect(no_table_path)) as conn:
        conn.execute("CREATE TABLE _tmp (x INT);")
        conn.execute("DROP TABLE _tmp;")
        conn.commit()
    empty_sqlite = no_table_path.read_bytes()

    with pytest.raises(DatabaseUploadError, match="zero user tables") as empty_exc:
        manager.upload_and_connect("no_tables.db", io.BytesIO(empty_sqlite))
    assert "no tables" in empty_exc.value.user_message

    assert service.is_connected is False
    # No invalid staged files should remain in upload_dir
    upload_dir = tmp_path / "uploads"
    if upload_dir.exists():
        assert list(upload_dir.iterdir()) == []


def test_upload_enforces_size_limit(tmp_path: Path) -> None:
    manager, _ = _make_manager(tmp_path, max_upload_mb=1)
    oversized = b"SQLite format 3\x00" + (b"x" * (1024 * 1024 + 64))
    with pytest.raises(DatabaseUploadTooLargeError, match="1 MB"):
        manager.upload_and_connect("big.db", io.BytesIO(oversized))


def test_failed_upload_preserves_existing_connected_database_and_switch_disconnect(
    tmp_path: Path,
) -> None:
    manager, service = _make_manager(tmp_path)
    db1 = _create_sqlite_bytes(
        tmp_path,
        "one.db",
        (
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT); "
            "INSERT INTO books VALUES (1, 'SQL');"
        ),
    )
    db2 = _create_sqlite_bytes(
        tmp_path,
        "two.sqlite",
        (
            "CREATE TABLE movies (id INTEGER PRIMARY KEY, name TEXT); "
            "INSERT INTO movies VALUES (1, 'Heat');"
        ),
    )

    e1, _ = manager.upload_and_connect("books.db", io.BytesIO(db1))
    e2, _ = manager.upload_and_connect("movies.sqlite", io.BytesIO(db2))
    assert manager.active_database == e2
    assert service.get_schema().table_names() == ("movies",)

    # Failed upload while DB #2 is active must NOT disturb DB #2
    with pytest.raises(DatabaseUploadError):
        manager.upload_and_connect("bad.db", io.BytesIO(b"corrupt data"))
    assert manager.active_database == e2
    assert service.get_schema().table_names() == ("movies",)

    # Switch back to DB #1
    manager.select_database(e1.id)
    assert manager.active_database == e1
    assert service.get_schema().table_names() == ("books",)

    # Unknown DB id must fail cleanly without dropping active DB
    with pytest.raises(DatabaseConnectionError):
        manager.select_database("nonexistent")
    assert manager.active_database == e1

    # Disconnect leaves QueryService cleanly disconnected
    manager.disconnect()
    assert manager.active_database is None
    assert service.is_connected is False


def test_batch_upload_15_valid_databases_and_duplicate_filenames(tmp_path: Path) -> None:
    manager, _ = _make_manager(tmp_path)
    files = []
    for idx in range(15):
        raw = _create_sqlite_bytes(
            tmp_path,
            f"src_{idx}.db",
            f"CREATE TABLE t_{idx} (id INTEGER PRIMARY KEY, val TEXT);",
        )
        # Include duplicate filenames ("shared.db") across multiple entries
        fname = "shared.db" if idx in (0, 5, 10) else f"db_{idx}.sqlite"
        files.append((fname, io.BytesIO(raw)))

    batch = manager.upload_batch(files)
    assert batch.uploaded_count == 15
    assert batch.rejected_count == 0
    assert len(batch.results) == 15

    library = manager.list_databases()
    assert len(library) == 15
    # Duplicate filenames must have distinct ids and non-overwriting paths
    stored_paths = {item.path for item in library}
    assert len(stored_paths) == 15
    shared_items = [item for item in library if item.name == "shared.db"]
    assert len(shared_items) == 3


def test_batch_upload_mixed_valid_and_invalid_isolates_failures(tmp_path: Path) -> None:
    manager, _ = _make_manager(tmp_path, max_upload_mb=1)
    valid_a = _create_sqlite_bytes(tmp_path, "a.db", "CREATE TABLE alpha (id INTEGER PRIMARY KEY);")
    valid_b = _create_sqlite_bytes(
        tmp_path, "b.sqlite3", "CREATE TABLE beta (id INTEGER PRIMARY KEY);"
    )
    empty_db = tmp_path / "empty.db"
    with closing(sqlite3.connect(empty_db)) as conn:
        conn.executescript("CREATE TABLE _tmp (x INT); DROP TABLE _tmp;")
        conn.commit()
    oversized = b"SQLite format 3\x00" + (b"x" * (1024 * 1024 + 32))

    batch = manager.upload_batch(
        [
            ("alpha.db", io.BytesIO(valid_a)),
            ("bad.txt", io.BytesIO(b"SQLite format 3\x00dummy")),
            ("../traversal.db", io.BytesIO(valid_a)),
            ("corrupt.db", io.BytesIO(b"not sqlite")),
            ("empty.db", io.BytesIO(empty_db.read_bytes())),
            ("too_big.db", io.BytesIO(oversized)),
            ("beta.sqlite3", io.BytesIO(valid_b)),
        ]
    )

    assert batch.uploaded_count == 2
    assert batch.rejected_count == 5
    statuses = [r.status for r in batch.results]
    assert statuses == [
        "uploaded",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
        "uploaded",
    ]
    assert [item.name for item in manager.list_databases()] == ["alpha.db", "beta.sqlite3"]


def test_batch_upload_enforces_max_15_files_and_total_batch_size_limit(tmp_path: Path) -> None:
    service = QueryService(
        inspector=SchemaInspector(None),
        provider=DeterministicFallbackProvider(),
        validator=SQLValidator(),
        executor=QueryExecutor(None),
        explainer=SQLExplainer(),
    )
    manager = DatabaseManager(
        upload_dir=tmp_path / "uploads",
        query_service=service,
        max_upload_size_mb=25,
        max_upload_files_per_batch=15,
        max_total_upload_batch_mb=2,
    )

    valid_small = _create_sqlite_bytes(tmp_path, "small.db", "CREATE TABLE x (id INT PRIMARY KEY);")

    # More than 15 files must be rejected cleanly on the backend
    too_many = [(f"db_{i}.db", io.BytesIO(valid_small)) for i in range(16)]
    with pytest.raises(DatabaseUploadError, match="exceeding limit of 15") as count_exc:
        manager.upload_batch(too_many)
    assert "Maximum allowed per upload batch is 15" in count_exc.value.user_message
    assert manager.list_databases() == []

    # Total batch size exceeding 2 MB (e.g., 3 files x 900 KB = 2.7 MB, each < 25 MB)
    chunk_900kb = b"SQLite format 3\x00" + (b"x" * (900 * 1024))
    oversized_batch = [(f"b_{i}.db", io.BytesIO(chunk_900kb)) for i in range(3)]
    with pytest.raises(DatabaseUploadTooLargeError, match="Total upload batch size"):
        manager.upload_batch(oversized_batch)
    assert manager.list_databases() == []
