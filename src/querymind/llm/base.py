"""Provider-independent LLM interface contract."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from querymind.domain.models import GeneratedSQL, SchemaSnapshot


@runtime_checkable
class BaseLLMProvider(Protocol):
    """Contract implemented by deterministic fallback and external LLM adapters."""

    name: str
    mode: Literal["fallback", "llm"]

    def generate_sql(self, question: str, schema: SchemaSnapshot) -> GeneratedSQL:
        """Convert a plain-English question into an unvalidated SQL candidate."""
        ...

    def explain_sql(self, sql: str, schema: SchemaSnapshot) -> str:
        """Explain a SQL query using only the SQL text and structural schema metadata."""
        ...
