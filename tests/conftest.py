"""Shared pytest fixtures for QueryMind tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from data.seed_sample_db import seed_database

from querymind.config import clear_settings_cache


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Generator[None, None, None]:
    """Ensure cached settings never leak across tests."""
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture()
def seeded_db_path(tmp_path: Path) -> Path:
    """Create an isolated seeded SQLite database under pytest's temp directory."""
    db_file = tmp_path / "sample querymind.db"
    return seed_database(db_file, overwrite=True)
