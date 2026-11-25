"""Metadata-only prompt builder for external LLM providers (never sends row data)."""

from __future__ import annotations

from querymind.domain.models import SchemaSnapshot


class PromptBuilder:
    """Constructs dialect-aware prompts using structural schema metadata only."""

    @staticmethod
    def build_sql_prompt(question: str, schema: SchemaSnapshot) -> str:
        """Build the prompt instructing an LLM to generate a single read-only SELECT query."""
        schema_text = schema.to_prompt_summary()
        return (
            "You are QueryMind, an expert SQLite data analyst.\n"
            "Convert the user's question into ONE safe, read-only SQLite SELECT query.\n\n"
            "STRICT RULES:\n"
            '1. Output ONLY a JSON object with keys "sql" and "explanation".\n'
            "2. Use ONLY the tables and columns listed in the schema below.\n"
            "3. Generate ONLY a single SELECT (or WITH ... SELECT) statement.\n"
            "4. Never use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA, ATTACH, "
            "or SQLite system tables (sqlite_master, sqlite_schema).\n"
            "5. Always qualify ambiguous columns with table names/aliases when joining.\n\n"
            f"{schema_text}\n\n"
            f"User question: {question.strip()}"
        )

    @staticmethod
    def build_explanation_prompt(sql: str, schema: SchemaSnapshot) -> str:
        """Build a prompt to explain a validated SQL query without sending table rows."""
        schema_text = schema.to_prompt_summary()
        return (
            "Explain the following SQLite SELECT query in 2-3 clear, non-technical sentences "
            "for a business user. Mention which tables, filters, groupings, sorting, "
            "and limits apply.\n\n"
            f"{schema_text}\n\n"
            f"SQL:\n{sql.strip()}"
        )
