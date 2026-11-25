"""Integration tests verifying Phase 1 foundation components work together."""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing
from pathlib import Path

from data.seed_sample_db import seed_database

from querymind.config import Settings
from querymind.domain.models import ValidatedQuery
from querymind.logging_config import configure_logging
from querymind.security.sanitizer import sanitize_error_message


def test_phase1_foundation_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("QUERYMIND_DB_PATH", raising=False)
    monkeypatch.delenv("QUERYMIND_GEMINI_API_KEY", raising=False)

    db_target = tmp_path / "folder with space" / "sample_querymind.db"
    seed_database(db_target, overwrite=True)

    env_file = tmp_path / ".env"
    secret_key = "AIzaIntegrationDummyKey1234567890123"
    env_file.write_text(
        f"QUERYMIND_DB_PATH={db_target}\n"
        f"QUERYMIND_GEMINI_API_KEY={secret_key}\n"
        "QUERYMIND_MAX_ROWS=100\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file, project_root=tmp_path)
    assert settings.resolved_db_path == db_target.resolve()
    assert settings.max_rows == 100

    logger = configure_logging(
        level=settings.log_level,
        secrets=settings.secret_values(),
        logger_name="querymind.phase1_integration",
    )
    stream = io.StringIO()
    logger.handlers[0].setStream(stream)  # type: ignore[attr-defined]
    logger.info("Initialized settings with key %s", secret_key)
    assert secret_key not in stream.getvalue()
    assert "[REDACTED]" in stream.getvalue()

    validated = ValidatedQuery(
        sql="SELECT id, total_amount FROM orders WHERE status = :status LIMIT 100",
        params={"status": "completed"},
        referenced_tables=("orders",),
        applied_limit=100,
    )

    with closing(sqlite3.connect(settings.sqlite_readonly_uri, uri=True)) as conn:
        rows = conn.execute(validated.sql, dict(validated.params)).fetchall()

    assert len(rows) == 4

    err_text = sanitize_error_message(
        RuntimeError(f"Failed at {db_target} using {secret_key}"),
        secrets=settings.secret_values(),
    )
    assert secret_key not in err_text
    assert str(tmp_path) not in err_text
