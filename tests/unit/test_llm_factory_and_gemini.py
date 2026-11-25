"""Unit and integration tests for LLM factory resolution and mocked Gemini adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from querymind.config import Settings
from querymind.db.executor import QueryExecutor
from querymind.db.inspector import SchemaInspector
from querymind.domain.exceptions import ConfigurationError, LLMProviderError, UnsafeSQLError
from querymind.domain.models import SchemaSnapshot
from querymind.llm.factory import create_llm_provider
from querymind.llm.providers.fallback import DeterministicFallbackProvider
from querymind.llm.providers.gemini_provider import GeminiProvider
from querymind.security.validator import SQLValidator


class _StubModels:
    def __init__(self, response_text: str | None = None, exc: Exception | None = None) -> None:
        self._response_text = response_text
        self._exc = exc
        self.last_prompt: str | None = None

    def generate_content(self, *, model: str, contents: str, config: dict) -> SimpleNamespace:
        del model, config
        self.last_prompt = contents
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(text=self._response_text)


class _StubClient:
    def __init__(self, models: _StubModels) -> None:
        self.models = models


def test_factory_selects_fallback_or_gemini_according_to_settings() -> None:
    auto_no_key = Settings(_env_file=None, llm_provider="auto")
    assert isinstance(create_llm_provider(auto_no_key), DeterministicFallbackProvider)

    auto_with_key = Settings(
        _env_file=None,
        llm_provider="auto",
        gemini_api_key="AIzaDummyKey1234567890123456",
    )
    assert isinstance(create_llm_provider(auto_with_key), GeminiProvider)

    forced_fallback = Settings(
        _env_file=None,
        llm_provider="fallback",
        gemini_api_key="AIzaDummyKey1234567890123456",
    )
    assert isinstance(create_llm_provider(forced_fallback), DeterministicFallbackProvider)

    with pytest.raises(ConfigurationError):
        create_llm_provider(Settings(_env_file=None, llm_provider="gemini"))


def test_gemini_provider_parses_json_and_masks_secret_on_error(seeded_db_path) -> None:
    schema = SchemaInspector(seeded_db_path).get_schema()
    secret = SecretStr("AIzaSySecretUnitKey123456789012345")

    ok_client = _StubClient(
        _StubModels(
            "```json\n"
            '{"sql": "SELECT name FROM customers WHERE country = \'India\'", '
            '"explanation": "Lists customers in India."}\n'
            "```"
        )
    )
    provider = GeminiProvider(api_key=secret, client=ok_client)
    gen = provider.generate_sql("Customers in India", schema)
    assert gen.mode == "llm"
    assert "customers" in gen.sql
    # Prompt must not contain seeded row values
    assert ok_client.models.last_prompt is not None
    assert "Aarav Sharma" not in ok_client.models.last_prompt

    validated = SQLValidator().validate(gen.sql, schema)
    result = QueryExecutor(seeded_db_path).execute(validated)
    assert result.rows == [{"name": "Aarav Sharma"}]

    # Error containing API key must be scrubbed
    err_client = _StubClient(
        _StubModels(exc=RuntimeError(f"403 Forbidden for key {secret.get_secret_value()}"))
    )
    bad_provider = GeminiProvider(api_key=secret, client=err_client)
    with pytest.raises(LLMProviderError) as exc_info:
        bad_provider.generate_sql("Customers in India", schema)
    assert secret.get_secret_value() not in str(exc_info.value)


def test_gemini_provider_rejects_malformed_json_and_unsafe_sql_is_blocked(seeded_db_path) -> None:
    schema: SchemaSnapshot = SchemaInspector(seeded_db_path).get_schema()
    secret = SecretStr("AIzaSySecretUnitKey123456789012345")

    bad_json_provider = GeminiProvider(api_key=secret, client=_StubClient(_StubModels("not json")))
    with pytest.raises(LLMProviderError):
        bad_json_provider.generate_sql("Show orders", schema)

    evil_sql_provider = GeminiProvider(
        api_key=secret,
        client=_StubClient(_StubModels('{"sql": "DROP TABLE orders", "explanation": "drops"}')),
    )
    gen = evil_sql_provider.generate_sql("Drop orders", schema)
    with pytest.raises(UnsafeSQLError):
        SQLValidator().validate(gen.sql, schema)
