"""End-to-end integration and security tests for the upload-to-query workflow."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from querymind.api.app import create_app
from querymind.config import Settings
from querymind.ui.api_client import QueryMindAPIClient


def _build_sqlite_bytes(tmp_path: Path, filename: str, schema_and_seed_sql: str) -> bytes:
    path = tmp_path / filename
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(schema_and_seed_sql)
        conn.commit()
    return path.read_bytes()


def test_full_upload_connect_query_switch_disconnect_workflow(tmp_path: Path) -> None:
    upload_dir = tmp_path / "managed_uploads"
    settings = Settings(
        _env_file=None,
        project_root=tmp_path,
        db_path=None,
        upload_dir=upload_dir,
        llm_provider="fallback",
        max_upload_size_mb=5,
    )
    app = create_app(settings)

    db1_bytes = _build_sqlite_bytes(
        tmp_path,
        "hr.db",
        """
        CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            salary REAL NOT NULL,
            department_id INTEGER NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments (id)
        );
        INSERT INTO departments VALUES (1, 'Engineering'), (2, 'Design');
        INSERT INTO employees VALUES (1, 'Alice', 120000, 1), (2, 'Bob', 95000, 2);
        """,
    )
    db2_bytes = _build_sqlite_bytes(
        tmp_path,
        "library.sqlite3",
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL, pages INTEGER NOT NULL);
        INSERT INTO books VALUES (1, 'Designing Data-Intensive Apps', 611);
        """,
    )

    with TestClient(app) as raw_client:
        api = QueryMindAPIClient(client=raw_client)

        # 1. Startup state: no database connected
        health0 = api.health()
        assert health0["status"] == "ok"
        assert health0["database_ready"] is False
        assert health0["database_name"] is None
        assert api.list_databases()["databases"] == []

        # Schema & query fail cleanly when disconnected
        schema_err = raw_client.get("/api/v1/schema")
        assert schema_err.status_code == 400
        assert schema_err.json()["code"] == "no_database_connected"

        # 2. Upload & connect DB #1
        up1 = api.upload_database("hr.db", db1_bytes)
        assert up1["database"]["name"] == "hr.db"
        assert [t["name"] for t in up1["schema_snapshot"]["tables"]] == [
            "departments",
            "employees",
        ]

        health1 = api.health()
        assert health1["database_ready"] is True
        assert health1["database_name"] == "hr.db"

        # 3. Query DB #1 in plain English
        q1 = api.ask_query("Show all employees")
        assert q1["row_count"] == 2
        assert q1["columns"] == ["id", "name", "salary", "department_id"]
        assert "LIMIT" in q1["sql"]
        assert q1["explanation"]

        # Read-only / SQL safety still enforced on uploaded DB
        bad_q = raw_client.post("/api/v1/query", json={"question": "Drop table employees"})
        assert bad_q.status_code == 400

        # 4. Upload DB #2 and verify active queries target DB #2
        up2 = api.upload_database("library.sqlite3", db2_bytes)
        assert up2["database"]["name"] == "library.sqlite3"
        q2 = api.ask_query("Show all books")
        assert q2["row_count"] == 1
        assert q2["rows"][0]["title"] == "Designing Data-Intensive Apps"

        # 5. Invalid upload must fail safely without exposing paths or dropping DB #2
        bad_upload = raw_client.post(
            "/api/v1/databases/upload",
            files={"file": ("../../evil.db", b"not sqlite", "application/octet-stream")},
        )
        assert bad_upload.status_code == 400
        assert str(upload_dir) not in bad_upload.text
        assert "Traceback" not in bad_upload.text
        assert api.health()["database_name"] == "library.sqlite3"

        # 6. Switch back to DB #1
        switched = api.select_database(up1["database"]["id"])
        assert switched["database"]["name"] == "hr.db"
        assert [t["name"] for t in api.get_schema()["tables"]] == ["departments", "employees"]

        # 7. Disconnect and verify no database is active
        disc = api.disconnect_database()
        assert disc["status"] == "disconnected"
        assert api.health()["database_ready"] is False
        assert api.health()["database_name"] is None


def test_multi_file_batch_upload_library_switching_and_readonly_enforcement(
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "batch_uploads"
    settings = Settings(
        _env_file=None,
        project_root=tmp_path,
        db_path=None,
        upload_dir=upload_dir,
        llm_provider="fallback",
        max_upload_size_mb=25,
        max_upload_files_per_batch=15,
        max_total_upload_batch_mb=100,
    )
    app = create_app(settings)

    db_a = _build_sqlite_bytes(
        tmp_path,
        "sales.db",
        "CREATE TABLE invoices (id INTEGER PRIMARY KEY, amount REAL NOT NULL); "
        "INSERT INTO invoices VALUES (1, 250.0), (2, 750.0);",
    )
    db_b = _build_sqlite_bytes(
        tmp_path,
        "inventory.sqlite",
        "CREATE TABLE items (id INTEGER PRIMARY KEY, sku TEXT NOT NULL); "
        "INSERT INTO items VALUES (1, 'SKU-100');",
    )

    with TestClient(app) as raw_client:
        api = QueryMindAPIClient(client=raw_client)

        # Upload mixed batch (2 valid + 1 invalid) via /api/v1/databases/upload-batch
        batch_resp = api.upload_databases_batch(
            [
                ("sales.db", db_a),
                ("bad.csv", b"col1,col2\n1,2"),
                ("inventory.sqlite", db_b),
            ]
        )
        assert batch_resp["total_files"] == 3
        assert batch_resp["uploaded_count"] == 2
        assert batch_resp["rejected_count"] == 1
        assert batch_resp["results"][1]["status"] == "rejected"

        # Library lists both valid databases
        lib = api.list_databases()
        assert len(lib["databases"]) == 2
        id_by_name = {d["name"]: d["id"] for d in lib["databases"]}

        # Select sales.db and query
        api.select_database(id_by_name["sales.db"])
        res_a = api.ask_query("What is the total amount of invoices?")
        assert res_a["rows"][0]["metric_value"] == 1000.0

        # Switch to inventory.sqlite without restarting; verify isolation + read-only safety
        api.select_database(id_by_name["inventory.sqlite"])
        res_b = api.ask_query("Show all items")
        assert res_b["rows"] == [{"id": 1, "sku": "SKU-100"}]

        # Querying table from sales.db must fail cleanly on inventory.sqlite
        wrong_db_err = raw_client.post("/api/v1/query", json={"question": "Show all invoices"})
        assert wrong_db_err.status_code == 400

        # Read-only / SQL safety remains active after switching
        unsafe_err = raw_client.post("/api/v1/query", json={"question": "Drop table items"})
        assert unsafe_err.status_code == 400

        # Exceeding 15 files via HTTP endpoint returns 400
        too_many_resp = raw_client.post(
            "/api/v1/databases/upload-batch",
            files=[("files", (f"f_{i}.db", db_a, "application/octet-stream")) for i in range(16)],
        )
        assert too_many_resp.status_code == 400
        assert str(upload_dir) not in too_many_resp.text
