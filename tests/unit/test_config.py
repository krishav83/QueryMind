"""Unit tests for Windows-safe Settings configuration."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from querymind.config import Settings, clear_settings_cache, get_settings


def test_settings_default_has_no_connected_database() -> None:
    settings = Settings(_env_file=None)
    assert settings.db_path is None
    assert settings.resolved_db_path is None
    assert settings.sqlite_readonly_uri is None
    assert settings.max_upload_size_mb == 25


def test_settings_resolve_relative_db_path_against_project_root(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        project_root=tmp_path,
        db_path="data/nested/test.db",
    )

    assert settings.project_root == tmp_path.resolve()
    assert settings.resolved_db_path == (tmp_path / "data" / "nested" / "test.db").resolve()


def test_settings_windows_path_with_spaces_produces_valid_sqlite_uri(
    seeded_db_path: Path,
) -> None:
    # seeded_db_path intentionally contains a space ("sample querymind.db")
    settings = Settings(_env_file=None, db_path=seeded_db_path)

    uri = settings.sqlite_readonly_uri
    assert uri.startswith("file:///")
    assert uri.endswith("?mode=ro")
    assert "%20" in uri

    with closing(sqlite3.connect(uri, uri=True)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    assert count == 5


def test_empty_api_keys_and_urls_normalize_to_none() -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="   ",
        openai_api_key="",
        openai_base_url="  ",
    )

    assert settings.gemini_api_key is None
    assert settings.openai_api_key is None
    assert settings.openai_base_url is None
    assert settings.secret_values() == ()


def test_settings_load_from_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUERYMIND_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("QUERYMIND_MAX_ROWS", raising=False)
    monkeypatch.delenv("QUERYMIND_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("QUERYMIND_OPENAI_API_KEY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "QUERYMIND_LLM_PROVIDER=gemini\n"
        "QUERYMIND_MAX_ROWS=250\n"
        "QUERYMIND_GEMINI_API_KEY=AIzaDummyTestKey1234567890123456\n"
        "QUERYMIND_OPENAI_API_KEY=sk-dummyOpenAIKey12345678901234\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file, project_root=tmp_path)

    assert settings.llm_provider == "gemini"
    assert settings.max_rows == 250
    assert settings.gemini_api_key is not None
    assert settings.openai_api_key is not None
    assert settings.secret_values() == (
        "AIzaDummyTestKey1234567890123456",
        "sk-dummyOpenAIKey12345678901234",
    )


def test_settings_accept_valid_windows_backslash_and_forward_slash_paths(tmp_path: Path) -> None:
    spaced_dir = tmp_path / "folder with space"
    spaced_dir.mkdir()
    db_file = spaced_dir / "my db.sqlite"
    db_file.touch()

    s_backslash = Settings(_env_file=None, project_root=str(tmp_path), db_path=str(db_file))
    s_forward = Settings(
        _env_file=None,
        project_root=tmp_path.as_posix(),
        db_path=db_file.as_posix(),
    )

    assert s_backslash.resolved_db_path == db_file.resolve()
    assert s_forward.resolved_db_path == db_file.resolve()
    assert s_backslash.sqlite_readonly_uri == s_forward.sqlite_readonly_uri


@pytest.mark.parametrize(
    "corrupted_path",
    [
        "C:/Users/\x07dmin/sample.db",
        "C:/Users/admin/\x0colder/sample.db",
        "C:/Users/admin/\test/sample.db",
        "C:/Users/admin/\nnew/sample.db",
    ],
)
def test_settings_reject_paths_with_control_characters(corrupted_path: str) -> None:
    with pytest.raises(ValidationError, match="control characters"):
        Settings(_env_file=None, db_path=corrupted_path)

    with pytest.raises(ValidationError, match="control characters"):
        Settings(_env_file=None, project_root=corrupted_path)


def test_settings_reject_invalid_bounds_and_empty_db_path() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, db_path="   ")

    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_rows=0)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, query_timeout_seconds=0.01)


def test_get_settings_cache_and_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUERYMIND_MAX_ROWS", "123")
    first = get_settings()
    assert first.max_rows == 123

    monkeypatch.setenv("QUERYMIND_MAX_ROWS", "456")
    assert get_settings().max_rows == 123

    clear_settings_cache()
    assert get_settings().max_rows == 456
