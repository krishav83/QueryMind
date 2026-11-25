"""Question input panel with dynamic schema-derived prompt suggestions."""

from __future__ import annotations

from typing import Any

import streamlit as st


def build_dynamic_suggestions(schema: dict[str, Any] | None) -> list[str]:
    """Generate starter prompt buttons from the currently connected database schema."""
    tables = (schema or {}).get("tables") or []
    if not tables:
        return []

    first_table = tables[0]["name"]
    suggestions = [
        f"Show all {first_table}",
        f"How many {first_table} are there?",
    ]
    if len(tables) >= 2:
        second_table = tables[1]["name"]
        suggestions.append(f"Show all {second_table}")
    return suggestions


def render_query_panel(schema: dict[str, Any] | None = None) -> tuple[str, int, bool]:
    """Render question input, dynamic schema suggestions, max-rows control, and submit."""
    st.subheader("Ask a Question in Plain English")

    suggestions = build_dynamic_suggestions(schema)
    if suggestions:
        preset_cols = st.columns(len(suggestions))
        for idx, sample in enumerate(suggestions):
            with preset_cols[idx]:
                if st.button(sample, key=f"preset_{idx}", use_container_width=True):
                    st.session_state["question_input"] = sample

    question = st.text_area(
        "Natural language question",
        key="question_input",
        placeholder="Ask a question about the tables in your connected database...",
        height=90,
    )

    col_limit, col_submit = st.columns([1, 3])
    with col_limit:
        max_rows = int(st.number_input("Max rows", min_value=1, max_value=5000, value=500, step=50))
    with col_submit:
        st.write("")
        st.write("")
        submitted = st.button("Generate & Run Safe SQL", type="primary", use_container_width=True)

    return question, max_rows, submitted
