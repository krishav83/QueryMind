"""Provider factory resolving Gemini or deterministic fallback from Settings."""

from __future__ import annotations

from querymind.config import Settings
from querymind.domain.exceptions import ConfigurationError
from querymind.llm.base import BaseLLMProvider
from querymind.llm.providers.fallback import DeterministicFallbackProvider
from querymind.llm.providers.gemini_provider import GeminiProvider


def create_llm_provider(settings: Settings) -> BaseLLMProvider:
    """Instantiate the active NL-to-SQL provider according to ``settings.llm_provider``."""
    choice = settings.llm_provider

    if choice == "fallback":
        return DeterministicFallbackProvider(default_limit=settings.max_rows)

    if choice == "gemini":
        if settings.gemini_api_key is None:
            raise ConfigurationError(
                "QUERYMIND_LLM_PROVIDER='gemini' requires QUERYMIND_GEMINI_API_KEY.",
                user_message=(
                    "Gemini provider is selected, but no Gemini API key is configured. "
                    "Set QUERYMIND_GEMINI_API_KEY or switch to fallback/auto mode."
                ),
            )
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    if choice == "auto":
        if settings.gemini_api_key is not None:
            return GeminiProvider(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        return DeterministicFallbackProvider(default_limit=settings.max_rows)

    raise ConfigurationError(
        f"Provider '{choice}' is not enabled in this build; use 'auto', 'fallback', or 'gemini'.",
        user_message=(f"Provider '{choice}' is not enabled. Use 'auto', 'fallback', or 'gemini'."),
    )
