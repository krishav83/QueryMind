"""Integration tests for FastAPI endpoints using TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

from querymind.api.app import create_app
from querymind.config import Settings


def test_api_health_schema_and_query_happy_path(seeded_db_path) -> None:
    settings = Settings(
        _env_file=None,
        db_path=seeded_db_path,
        llm_provider="fallback",
        max_rows=50,
    )
    app = create_app(settings)
    client = TestClient(app)

    health_resp = client.get("/api/v1/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["status"] == "ok"
    assert health_data["provider_mode"] == "fallback"
    assert health_data["database_ready"] is True

    schema_resp = client.get("/api/v1/schema")
    assert schema_resp.status_code == 200
    table_names = [t["name"] for t in schema_resp.json()["tables"]]
    assert table_names == ["customers", "order_items", "orders", "products"]

    refresh_resp = client.post("/api/v1/schema/refresh")
    assert refresh_resp.status_code == 200

    query_resp = client.post(
        "/api/v1/query",
        json={"question": "Show top 3 products by unit_price"},
    )
    assert query_resp.status_code == 200
    payload = query_resp.json()
    assert payload["row_count"] == 3
    assert payload["columns"]
    assert payload["explanation"]
    assert "LIMIT 3" in payload["sql"]


def test_api_returns_400_on_unsupported_question_and_422_on_blank_question(
    seeded_db_path,
) -> None:
    settings = Settings(_env_file=None, db_path=seeded_db_path, llm_provider="fallback")
    client = TestClient(create_app(settings))

    bad_req = client.post("/api/v1/query", json={"question": "   "})
    assert bad_req.status_code == 422

    unsupported = client.post("/api/v1/query", json={"question": "Drop table orders"})
    assert unsupported.status_code == 400
    assert unsupported.json()["code"] == "unsupported_deterministic_query"
