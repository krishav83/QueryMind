"""Unit tests verifying metadata-only LLM prompt construction."""

from __future__ import annotations

from querymind.domain.models import ColumnInfo, ForeignKeyInfo, SchemaSnapshot, TableInfo
from querymind.llm.prompts import PromptBuilder


def test_prompts_include_structural_schema_and_never_row_values() -> None:
    schema = SchemaSnapshot(
        dialect="sqlite",
        tables=(
            TableInfo(
                name="customers",
                columns=(
                    ColumnInfo(name="id", data_type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="email", data_type="TEXT", nullable=False),
                ),
            ),
            TableInfo(
                name="orders",
                columns=(
                    ColumnInfo(name="id", data_type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="customer_id", data_type="INTEGER", nullable=False),
                ),
                foreign_keys=(
                    ForeignKeyInfo(
                        column="customer_id",
                        referred_table="customers",
                        referred_column="id",
                    ),
                ),
            ),
        ),
    )

    sql_prompt = PromptBuilder.build_sql_prompt("Count orders per customer", schema)
    assert "customers: id (INTEGER, PK, NOT NULL), email (TEXT, NOT NULL)" in sql_prompt
    assert "FK: orders.customer_id -> customers.id" in sql_prompt
    assert "Count orders per customer" in sql_prompt
    # Ensure no sample DB row strings exist anywhere in prompt
    assert "aarav.sharma@example.com" not in sql_prompt

    explain_prompt = PromptBuilder.build_explanation_prompt("SELECT COUNT(*) FROM orders", schema)
    assert "SELECT COUNT(*) FROM orders" in explain_prompt
    assert "customers:" in explain_prompt
