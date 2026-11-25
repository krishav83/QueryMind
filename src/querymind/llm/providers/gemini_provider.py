"""Google Gemini adapter implementing the provider-independent BaseLLMProvider contract."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import SecretStr

from querymind.domain.exceptions import LLMProviderError
from querymind.domain.models import GeneratedSQL, SchemaSnapshot
from querymind.llm.prompts import PromptBuilder
from querymind.security.sanitizer import sanitize_error_message

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class GeminiProvider:
    """External LLM provider backed by the official ``google-genai`` SDK."""

    name = "gemini"
    mode = "llm"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str = "gemini-2.5-flash",
        timeout_seconds: float = 20.0,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise LLMProviderError(
                "google-genai package is not installed.",
                user_message="Gemini SDK is not installed.",
            ) from exc

        http_opts = types.HttpOptions(timeout=int(self._timeout_seconds * 1000))
        self._client = genai.Client(
            api_key=self._api_key.get_secret_value(),
            http_options=http_opts,
        )
        return self._client

    def generate_sql(self, question: str, schema: SchemaSnapshot) -> GeneratedSQL:
        """Generate an unvalidated SQL candidate from plain English using Gemini."""
        prompt = PromptBuilder.build_sql_prompt(question, schema)
        raw_text = self._invoke_model(prompt, expect_json=True)
        payload = self._parse_json_response(raw_text)

        sql = str(payload.get("sql") or "").strip()
        if not sql:
            raise LLMProviderError(
                "Gemini response did not include a SQL query.",
                user_message="Gemini did not return a valid SQL query.",
            )

        explanation = payload.get("explanation")
        return GeneratedSQL(
            sql=sql,
            params={},
            provider_name=f"{self.name}:{self._model}",
            mode="llm",
            raw_explanation=str(explanation).strip() if explanation else None,
        )

    def explain_sql(self, sql: str, schema: SchemaSnapshot) -> str:
        """Explain ``sql`` using Gemini with metadata-only context."""
        prompt = PromptBuilder.build_explanation_prompt(sql, schema)
        text = self._invoke_model(prompt, expect_json=False).strip()
        if not text:
            raise LLMProviderError(
                "Gemini returned an empty SQL explanation.",
                user_message="Unable to generate SQL explanation.",
            )
        return text

    def _invoke_model(self, prompt: str, *, expect_json: bool) -> str:
        secret = self._api_key.get_secret_value()
        try:
            client = self._get_client()
            config: dict[str, Any] = {"temperature": 0.1}
            if expect_json:
                config["response_mime_type"] = "application/json"

            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise LLMProviderError(
                    "Gemini returned an empty response.",
                    user_message="Gemini returned an empty response.",
                )
            return text
        except LLMProviderError:
            raise
        except Exception as exc:
            safe_msg = sanitize_error_message(exc, secrets=(secret,))
            raise LLMProviderError(
                f"Gemini request failed: {safe_msg}",
                user_message=(
                    "External LLM request failed. You can switch to deterministic fallback mode."
                ),
            ) from exc

    @staticmethod
    def _parse_json_response(raw_text: str) -> dict[str, Any]:
        cleaned = _FENCE_RE.sub("", raw_text.strip()).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"Gemini returned malformed JSON: {exc}",
                user_message="Gemini returned an unreadable response format.",
            ) from exc
        if not isinstance(parsed, dict):
            raise LLMProviderError(
                "Gemini JSON response was not an object.",
                user_message="Gemini returned an unexpected response format.",
            )
        return parsed
