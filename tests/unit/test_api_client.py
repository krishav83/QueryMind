"""Unit tests for the Streamlit HTTP API client."""

from __future__ import annotations

import httpx
import pytest

from querymind.ui.api_client import (
    DEFAULT_API_URL,
    APIClientError,
    QueryMindAPIClient,
    get_api_base_url,
)
from querymind.ui.components.query_panel import build_dynamic_suggestions


def test_api_client_default_and_env_url_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUERYMIND_API_URL", raising=False)
    assert DEFAULT_API_URL == "http://127.0.0.1:8001"
    assert get_api_base_url() == "http://127.0.0.1:8001"
    assert QueryMindAPIClient()._base_url == "http://127.0.0.1:8001"

    monkeypatch.setenv("QUERYMIND_API_URL", "http://127.0.0.1:9005/")
    assert get_api_base_url() == "http://127.0.0.1:9005"
    assert QueryMindAPIClient()._base_url == "http://127.0.0.1:9005"

    explicit = QueryMindAPIClient(base_url="http://127.0.0.1:8123/")
    assert explicit._base_url == "http://127.0.0.1:8123"


def test_api_client_handles_success_and_error_responses() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok", "provider_mode": "fallback"})
        if request.url.path == "/api/v1/query":
            return httpx.Response(
                400,
                json={
                    "detail": "Only read-only SELECT queries are permitted.",
                    "code": "unsafe_sql",
                },
            )
        return httpx.Response(404, json={"detail": "Not found"})

    transport = httpx.MockTransport(_handler)
    with httpx.Client(base_url="http://testserver", transport=transport) as raw_client:
        api = QueryMindAPIClient(client=raw_client)
        assert api.health()["status"] == "ok"

        with pytest.raises(APIClientError) as exc_info:
            api.ask_query("Drop table orders")
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "unsafe_sql"
        assert "read-only" in str(exc_info.value)


def test_api_client_handles_timeout_and_connection_errors() -> None:
    def _timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with httpx.Client(
        base_url="http://testserver",
        transport=httpx.MockTransport(_timeout_handler),
    ) as raw_client:
        api = QueryMindAPIClient(client=raw_client)
        with pytest.raises(APIClientError, match="timed out"):
            api.get_schema()


def test_build_dynamic_suggestions_adapts_to_connected_schema() -> None:
    assert build_dynamic_suggestions(None) == []
    assert build_dynamic_suggestions({"tables": []}) == []

    schema = {"tables": [{"name": "employees"}, {"name": "departments"}]}
    suggestions = build_dynamic_suggestions(schema)
    assert suggestions == [
        "Show all employees",
        "How many employees are there?",
        "Show all departments",
    ]
