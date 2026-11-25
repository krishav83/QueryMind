"""Sidebar component displaying connection status, database controls, and schema."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from querymind.ui.components.upload_welcome import render_switch_upload_widget


def render_schema_sidebar(
    health: dict[str, Any] | None,
    schema: dict[str, Any] | None,
    databases_state: dict[str, Any] | None,
    *,
    on_refresh: Callable[[], None],
    on_upload_files: Callable[[list[tuple[str, bytes]]], None],
    on_select: Callable[[str], None],
    on_disconnect: Callable[[], None],
) -> None:
    """Render database connection status, management actions, and schema explorer."""
    with st.sidebar:
        st.header("QueryMind Status")
        db_ok = bool(health and health.get("database_ready"))
        db_name = (health or {}).get("database_name")

        if health:
            if db_ok:
                st.success(f"Connected: {db_name or 'SQLite DB'}")
            else:
                st.info("No database connected")
            st.caption(
                f"Mode: `{health.get('provider_mode', 'unknown')}` "
                f"(`{health.get('provider_name', 'unknown')}`)"
            )
        else:
            st.warning("Backend status unavailable")

        render_switch_upload_widget(
            databases_state,
            on_upload_files=on_upload_files,
            on_select=on_select,
            on_disconnect=on_disconnect,
        )

        st.divider()
        col_title, col_btn = st.columns([2, 1])
        with col_title:
            st.subheader("Schema")
        with col_btn:
            if st.button("Refresh", use_container_width=True, disabled=not db_ok):
                on_refresh()

        if not db_ok or not schema or not schema.get("tables"):
            st.caption("Connect a SQLite database to inspect its tables and columns.")
            return

        st.caption(f"Dialect: `{schema.get('dialect', 'sqlite')}`")
        for table in schema["tables"]:
            label = f"{table['name']} ({len(table.get('columns', []))} cols)"
            with st.expander(label, expanded=False):
                for col in table.get("columns", []):
                    flags = []
                    if col.get("primary_key"):
                        flags.append("PK")
                    if not col.get("nullable", True):
                        flags.append("NOT NULL")
                    suffix = f" [{', '.join(flags)}]" if flags else ""
                    st.markdown(f"- `{col['name']}`: {col['data_type']}{suffix}")
                for fk in table.get("foreign_keys", []):
                    st.caption(
                        f"FK: `{fk['column']}` -> `{fk['referred_table']}.{fk['referred_column']}`"
                    )
