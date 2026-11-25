"""Welcome, multi-file SQLite upload, validation results, and Database Library UI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

ALLOWED_EXTENSIONS = ("db", "sqlite", "sqlite3")
MAX_BATCH_FILES = 15
MAX_FILE_MB = 25
MAX_TOTAL_BATCH_MB = 100


def _render_selected_preview(uploaded_files: list[Any]) -> bool:
    """Display selected filenames before upload and check client-side guardrails."""
    if not uploaded_files:
        return True

    total_bytes = sum(len(f.getvalue()) for f in uploaded_files)
    total_mb = total_bytes / (1024.0 * 1024.0)

    preview_rows = [
        {
            "Filename": f.name,
            "Size (KB)": round(len(f.getvalue()) / 1024.0, 1),
        }
        for f in uploaded_files
    ]
    st.caption(
        f"Selected {len(uploaded_files)} file(s) ({total_mb:.2f} MB total). "
        f"Limits: max {MAX_BATCH_FILES} files, {MAX_FILE_MB} MB/file, "
        f"{MAX_TOTAL_BATCH_MB} MB total."
    )
    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

    if len(uploaded_files) > MAX_BATCH_FILES:
        st.warning(
            f"Too many files selected ({len(uploaded_files)}). "
            f"Please select at most {MAX_BATCH_FILES} files per upload."
        )
        return False
    if total_mb > MAX_TOTAL_BATCH_MB:
        st.warning(
            f"Total batch size ({total_mb:.1f} MB) exceeds the {MAX_TOTAL_BATCH_MB} MB limit."
        )
        return False
    return True


def render_batch_upload_results(batch_report: dict[str, Any] | None) -> None:
    """Render per-file upload outcomes (Uploaded successfully vs Rejected + reason)."""
    if not batch_report:
        return

    results = batch_report.get("results") or []
    if not results:
        return

    uploaded_n = batch_report.get("uploaded_count", 0)
    rejected_n = batch_report.get("rejected_count", 0)
    st.markdown(f"**Upload Summary:** {uploaded_n} uploaded successfully, {rejected_n} rejected.")
    for item in results:
        fname = item.get("filename", "unknown")
        status = item.get("status", "rejected")
        msg = item.get("message", "")
        if status == "uploaded":
            db_meta = item.get("database") or {}
            tables_n = db_meta.get("table_count", 0)
            st.success(f"`{fname}` — {msg} ({tables_n} table(s))")
        else:
            st.error(f"`{fname}` — Rejected: {msg}")


def render_database_library(
    databases_state: dict[str, Any] | None,
    *,
    on_select: Callable[[str], None],
    on_disconnect: Callable[[], None],
    key_prefix: str = "welcome",
) -> None:
    """Render the Database Library showing all uploaded databases and active controls."""
    dbs = (databases_state or {}).get("databases") or []
    active_id = (databases_state or {}).get("active_id")
    if not dbs:
        return

    st.subheader("Database Library")
    rows = []
    for item in dbs:
        is_active = item["id"] == active_id
        rows.append(
            {
                "Filename": item["name"],
                "Status": "Active" if is_active else item.get("status", "ready").title(),
                "Size (KB)": round(item.get("size_bytes", 0) / 1024.0, 1),
                "Tables": item.get("table_count", 0),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    options = {item["id"]: f"{item['name']} ({item['table_count']} tables)" for item in dbs}
    ids = list(options.keys())
    default_idx = ids.index(active_id) if active_id in ids else 0

    col_sel, col_btn = st.columns([3, 1])
    with col_sel:
        selected_id = st.selectbox(
            "Select active database",
            options=ids,
            index=default_idx,
            format_func=lambda db_id: options[db_id],
            key=f"{key_prefix}_library_select",
        )
    with col_btn:
        st.write("")
        st.write("")
        if st.button("Connect", use_container_width=True, key=f"{key_prefix}_library_connect"):
            on_select(selected_id)

    if active_id is not None:
        if st.button(
            "Disconnect Database",
            use_container_width=False,
            key=f"{key_prefix}_library_disconnect",
        ):
            on_disconnect()


def render_welcome_upload(
    *,
    databases_state: dict[str, Any] | None = None,
    batch_report: dict[str, Any] | None = None,
    on_upload_files: Callable[[list[tuple[str, bytes]]], None],
    on_select: Callable[[str], None],
    on_disconnect: Callable[[], None],
    upload_error: str | None = None,
    upload_success: str | None = None,
) -> None:
    """Render the onboarding screen with single or multi-file SQLite upload and library."""
    st.markdown("## Welcome to QueryMind")
    st.markdown("Connect your database to start asking questions in plain English.")

    if upload_success:
        st.success(upload_success)
    if upload_error:
        st.error(upload_error)

    uploaded_files = st.file_uploader(
        "Upload SQLite databases (.db, .sqlite, .sqlite3 — up to 15 files)",
        type=list(ALLOWED_EXTENSIONS),
        accept_multiple_files=True,
        key="welcome_db_uploader",
        help="Drag and drop one or up to 15 SQLite files here, or click Browse files.",
    )

    file_list = list(uploaded_files) if uploaded_files else []
    within_limits = _render_selected_preview(file_list)

    btn_label = "Connect" if len(file_list) <= 1 else f"Upload {len(file_list)} Databases"
    if st.button(
        btn_label,
        type="primary",
        disabled=not file_list or not within_limits,
        use_container_width=False,
        key="welcome_connect_btn",
    ):
        on_upload_files([(f.name, f.getvalue()) for f in file_list])

    render_batch_upload_results(batch_report)
    render_database_library(
        databases_state,
        on_select=on_select,
        on_disconnect=on_disconnect,
        key_prefix="welcome",
    )


def render_switch_upload_widget(
    databases_state: dict[str, Any] | None,
    *,
    on_upload_files: Callable[[list[tuple[str, bytes]]], None],
    on_select: Callable[[str], None],
    on_disconnect: Callable[[], None],
) -> None:
    """Render database switching, multi-file upload, and disconnect controls in sidebar."""
    dbs = (databases_state or {}).get("databases", [])
    active_id = (databases_state or {}).get("active_id")

    if active_id is not None:
        if st.button("Disconnect Database", use_container_width=True, key="disconnect_db_btn"):
            on_disconnect()

    if dbs:
        st.caption(f"Database Library ({len(dbs)} uploaded)")
        options = {
            item["id"]: (
                f"{item['name']} — {round(item.get('size_bytes', 0) / 1024.0, 1)} KB, "
                f"{item['table_count']} tables"
            )
            for item in dbs
        }
        ids = list(options.keys())
        default_idx = ids.index(active_id) if active_id in ids else 0
        selected_id = st.selectbox(
            "Switch database",
            options=ids,
            index=default_idx,
            format_func=lambda db_id: options[db_id],
            key="switch_db_select",
        )
        if st.button("Switch", use_container_width=True, key="switch_db_btn"):
            on_select(selected_id)

    with st.expander("Upload More Databases", expanded=False):
        extra = st.file_uploader(
            "Choose SQLite files (max 15)",
            type=list(ALLOWED_EXTENSIONS),
            accept_multiple_files=True,
            key="sidebar_db_uploader",
        )
        extra_list = list(extra) if extra else []
        if extra_list:
            st.caption(f"{len(extra_list)} file(s) selected")
        if st.button(
            "Upload Selected",
            disabled=not extra_list or len(extra_list) > MAX_BATCH_FILES,
            use_container_width=True,
            key="sidebar_connect_btn",
        ):
            on_upload_files([(f.name, f.getvalue()) for f in extra_list])
