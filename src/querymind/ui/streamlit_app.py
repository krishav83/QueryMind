"""Streamlit application entry point for QueryMind."""

from __future__ import annotations

import streamlit as st

from querymind.ui.api_client import APIClientError, QueryMindAPIClient, get_api_base_url
from querymind.ui.components.query_panel import render_query_panel
from querymind.ui.components.results_view import render_results_view
from querymind.ui.components.schema_sidebar import render_schema_sidebar
from querymind.ui.components.upload_welcome import (
    render_batch_upload_results,
    render_database_library,
    render_welcome_upload,
)


def main() -> None:
    """Render the QueryMind Streamlit interface."""
    st.set_page_config(page_title="QueryMind — AI SQL Assistant", layout="wide")
    st.title("QueryMind")
    st.caption("AI-Powered Natural Language to Read-Only SQL Assistant")

    api_url = get_api_base_url()
    client = QueryMindAPIClient(base_url=api_url)

    def _sync_state(*, force_refresh_schema: bool = False) -> None:
        try:
            health = client.health()
            st.session_state["health"] = health
            st.session_state["databases"] = client.list_databases()
            if health.get("database_ready"):
                st.session_state["schema"] = (
                    client.refresh_schema() if force_refresh_schema else client.get_schema()
                )
            else:
                st.session_state.pop("schema", None)
            st.session_state.pop("backend_error", None)
        except APIClientError as exc:
            st.session_state["backend_error"] = str(exc)

    def _handle_upload_files(files: list[tuple[str, bytes]]) -> None:
        if not files:
            return
        active_id = (st.session_state.get("databases") or {}).get("active_id")

        # Preserve seamless 1-click connect when uploading a single file with no active DB.
        if len(files) == 1 and active_id is None:
            filename, content = files[0]
            try:
                result = client.upload_database(filename, content)
                db_info = result.get("database", {})
                st.session_state["schema"] = result.get("schema_snapshot")
                st.session_state["health"] = client.health()
                st.session_state["databases"] = client.list_databases()
                st.session_state["upload_success"] = (
                    f"Connected to `{db_info.get('name', filename)}` "
                    f"({db_info.get('table_count', 0)} table(s) discovered)."
                )
                st.session_state.pop("batch_report", None)
                st.session_state.pop("upload_error", None)
                st.session_state.pop("query_error", None)
                st.session_state.pop("last_response", None)
                st.rerun()
            except APIClientError as exc:
                st.session_state["upload_error"] = str(exc)
                st.session_state.pop("upload_success", None)
            return

        progress = st.progress(0.1, text=f"Uploading and validating {len(files)} database(s)...")
        try:
            report = client.upload_databases_batch(files, auto_connect=False)
            progress.progress(1.0, text="Upload validation complete.")
            st.session_state["batch_report"] = report
            st.session_state["health"] = client.health()
            st.session_state["databases"] = client.list_databases()
            st.session_state.pop("upload_error", None)
            st.session_state.pop("upload_success", None)
            st.rerun()
        except APIClientError as exc:
            progress.empty()
            st.session_state["upload_error"] = str(exc)
            st.session_state.pop("upload_success", None)

    def _handle_select(database_id: str) -> None:
        try:
            result = client.select_database(database_id)
            db_info = result.get("database", {})
            st.session_state["schema"] = result.get("schema_snapshot")
            st.session_state["health"] = client.health()
            st.session_state["databases"] = client.list_databases()
            st.session_state["upload_success"] = (
                f"Connected to `{db_info.get('name', database_id)}`."
            )
            st.session_state.pop("batch_report", None)
            st.session_state.pop("upload_error", None)
            st.session_state.pop("query_error", None)
            st.session_state.pop("last_response", None)
            st.rerun()
        except APIClientError as exc:
            st.session_state["upload_error"] = str(exc)

    def _handle_disconnect() -> None:
        try:
            client.disconnect_database()
            st.session_state["health"] = client.health()
            st.session_state["databases"] = client.list_databases()
            st.session_state.pop("schema", None)
            st.session_state.pop("last_response", None)
            st.session_state.pop("query_error", None)
            st.session_state.pop("upload_success", None)
            st.session_state.pop("upload_error", None)
            st.session_state.pop("batch_report", None)
            st.rerun()
        except APIClientError as exc:
            st.session_state["upload_error"] = str(exc)

    if "health" not in st.session_state:
        _sync_state(force_refresh_schema=False)

    render_schema_sidebar(
        st.session_state.get("health"),
        st.session_state.get("schema"),
        st.session_state.get("databases"),
        on_refresh=lambda: _sync_state(force_refresh_schema=True),
        on_upload_files=_handle_upload_files,
        on_select=_handle_select,
        on_disconnect=_handle_disconnect,
    )

    if "backend_error" in st.session_state:
        st.error(
            f"Backend connection issue: {st.session_state['backend_error']} "
            f"(Expected FastAPI at {api_url})"
        )

    health = st.session_state.get("health") or {}
    if not health.get("database_ready"):
        render_welcome_upload(
            databases_state=st.session_state.get("databases"),
            batch_report=st.session_state.get("batch_report"),
            on_upload_files=_handle_upload_files,
            on_select=_handle_select,
            on_disconnect=_handle_disconnect,
            upload_error=st.session_state.get("upload_error"),
            upload_success=st.session_state.get("upload_success"),
        )
        return

    if st.session_state.get("upload_success"):
        st.success(st.session_state.pop("upload_success"))
    if st.session_state.get("upload_error"):
        st.error(st.session_state.pop("upload_error"))
    if st.session_state.get("batch_report"):
        with st.expander("Latest Batch Upload Results", expanded=True):
            render_batch_upload_results(st.session_state.pop("batch_report"))

    question, max_rows, submitted = render_query_panel(st.session_state.get("schema"))

    if submitted:
        if not question.strip():
            st.warning("Please enter a question in plain English.")
        else:
            with st.spinner("Generating, validating, and executing read-only SQL..."):
                try:
                    st.session_state["last_response"] = client.ask_query(
                        question.strip(),
                        max_rows=max_rows,
                    )
                    st.session_state.pop("query_error", None)
                except APIClientError as exc:
                    st.session_state["query_error"] = str(exc)
                    st.session_state.pop("last_response", None)

    if "query_error" in st.session_state:
        st.error(st.session_state["query_error"])

    if "last_response" in st.session_state:
        render_results_view(st.session_state["last_response"])

    with st.expander("Database Library", expanded=False):
        render_database_library(
            st.session_state.get("databases"),
            on_select=_handle_select,
            on_disconnect=_handle_disconnect,
            key_prefix="main",
        )


if __name__ == "__main__":
    main()
