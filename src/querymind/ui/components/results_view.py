"""Results view rendering validated SQL, explanation, Pandas table, and CSV export."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


def render_results_view(response: dict[str, Any]) -> None:
    """Display SQL, plain-English explanation, execution metrics, and tabular results."""
    st.divider()
    m1, m2, m3 = st.columns(3)
    m1.metric("Rows Returned", response.get("row_count", 0))
    m2.metric("Execution Time (ms)", response.get("execution_time_ms", 0.0))
    m3.metric("Provider Mode", f"{response.get('provider_mode')} ({response.get('provider_name')})")

    if response.get("truncated"):
        st.warning("Results were truncated to the configured row limit.")

    col_sql, col_exp = st.columns(2)
    with col_sql:
        st.subheader("Validated Read-Only SQL")
        st.code(response.get("sql", ""), language="sql")
    with col_exp:
        st.subheader("SQL Explanation")
        st.info(response.get("explanation", ""))

    st.subheader("Query Results")
    columns = response.get("columns", [])
    rows = response.get("rows", [])
    frame = pd.DataFrame(rows, columns=columns)

    if frame.empty:
        st.info("Query executed successfully and returned 0 rows.")
    else:
        st.dataframe(frame, use_container_width=True, hide_index=True)
        csv_bytes = frame.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Results as CSV",
            data=csv_bytes,
            file_name="querymind_results.csv",
            mime="text/csv",
        )
