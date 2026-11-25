"""Security tests ensuring API responses never leak keys, DB paths, or tracebacks."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from querymind.api.app import create_app
from querymind.config import Settings
from querymind.domain.exceptions import LLMProviderError
from querymind.domain.models import GeneratedSQL


def test_api_health_and_errors_never_expose_secrets_or_db_paths(seeded_db_path) -> None:
    secret_key = "AIzaSyApiSecuritySecret123456789012"
    settings = Settings(
        _env_file=None,
        db_path=seeded_db_path,
        llm_provider="fallback",
        gemini_api_key=secret_key,
    )
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    health_text = client.get("/api/v1/health").text
    assert secret_key not in health_text
    assert str(seeded_db_path) not in health_text

    # Simulate an unsafe SQL payload returned by a compromised provider
    evil_provider = MagicMock()
    evil_provider.mode = "llm"
    evil_provider.name = "mock_evil"
    evil_provider.generate_sql.return_value = GeneratedSQL(
        sql="SELECT name FROM sqlite_master",
        params={},
        provider_name="mock_evil",
        mode="llm",
    )
    app.state.query_service._provider = evil_provider

    resp = client.post("/api/v1/query", json={"question": "Show tables"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "unsafe_sql"
    assert secret_key not in resp.text
    assert str(seeded_db_path) not in resp.text

    # Simulate an upstream exception containing secret + Windows path
    failing_provider = MagicMock()
    failing_provider.mode = "llm"
    failing_provider.name = "mock_fail"
    failing_provider.generate_sql.side_effect = LLMProviderError(
        f"Failed at {seeded_db_path} with {secret_key}",
        user_message=f"Provider error at {seeded_db_path} using {secret_key}",
    )
    app.state.query_service._provider = failing_provider

    err_resp = client.post("/api/v1/query", json={"question": "List customers"})
    assert err_resp.status_code == 502
    assert secret_key not in err_resp.text
    assert str(seeded_db_path) not in err_resp.text
    assert "Traceback" not in err_resp.text
