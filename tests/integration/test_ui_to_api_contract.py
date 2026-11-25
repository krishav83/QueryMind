"""End-to-end contract tests connecting QueryMindAPIClient directly to FastAPI app."""

from __future__ import annotations

from fastapi.testclient import TestClient

from querymind.api.app import create_app
from querymind.config import Settings
from querymind.ui.api_client import QueryMindAPIClient


def test_ui_client_to_fastapi_end_to_end_contract(seeded_db_path) -> None:
    settings = Settings(
        _env_file=None,
        db_path=seeded_db_path,
        llm_provider="fallback",
        max_rows=50,
    )
    app = create_app(settings)

    with TestClient(app) as raw_client:
        api = QueryMindAPIClient(client=raw_client)

        health = api.health()
        assert health["database_ready"] is True
        assert health["provider_mode"] == "fallback"

        schema = api.get_schema()
        assert len(schema["tables"]) == 4

        refreshed = api.refresh_schema()
        assert refreshed == schema

        resp = api.ask_query("Count orders by status", max_rows=10)
        assert resp["provider_mode"] == "fallback"
        assert resp["row_count"] == 3
        assert resp["sql"]
        assert resp["explanation"]
