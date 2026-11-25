"""Application configuration with Windows-safe path handling and secret masking."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderChoice = Literal["auto", "fallback", "gemini", "openai_compatible"]
AppEnv = Literal["development", "testing", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _default_project_root() -> Path:
    """Resolve the repository root relative to this module."""
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed runtime settings loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="QUERYMIND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "development"
    project_root: Path = Field(default_factory=_default_project_root)
    db_path: Path | None = None
    upload_dir: Path = Field(default=Path("data/uploads"))
    max_upload_size_mb: int = Field(default=25, ge=1, le=200)
    max_upload_files_per_batch: int = Field(default=15, ge=1, le=50)
    max_total_upload_batch_mb: int = Field(default=100, ge=1, le=500)

    llm_provider: LLMProviderChoice = "auto"

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"

    llm_timeout_seconds: float = Field(default=20.0, gt=0.0, le=120.0)
    max_rows: int = Field(default=500, ge=1, le=5000)
    query_timeout_seconds: float = Field(default=5.0, gt=0.1, le=60.0)

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8001, ge=1, le=65535)
    log_level: LogLevel = "INFO"

    @field_validator("gemini_api_key", "openai_api_key", mode="before")
    @classmethod
    def _empty_secret_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def _empty_url_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("project_root", "upload_dir", mode="before")
    @classmethod
    def _coerce_required_path(cls, value: object) -> Path:
        return cls._parse_path_value(value)

    @field_validator("db_path", mode="before")
    @classmethod
    def _coerce_optional_path(cls, value: object) -> Path | None:
        if value is None or value == "":
            return None
        return cls._parse_path_value(value)

    @staticmethod
    def _parse_path_value(value: object) -> Path:
        if isinstance(value, Path):
            raw_str = str(value)
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_str):
                raise ValueError(
                    "Path configuration value contains invalid control characters "
                    "(such as '\\a', '\\f', '\\t', or '\\n'). On Windows .env files, "
                    "use forward slashes or unquoted paths instead of double-quoted backslashes."
                )
            return value.expanduser()
        if isinstance(value, str):
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                raise ValueError(
                    "Path configuration value contains invalid control characters "
                    "(such as '\\a', '\\f', '\\t', or '\\n'). On Windows .env files, "
                    "use forward slashes or unquoted paths instead of double-quoted backslashes."
                )
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("Path configuration value cannot be empty.")
            return Path(cleaned).expanduser()
        raise TypeError(f"Expected Path or str, got {type(value).__name__}")

    @model_validator(mode="after")
    def _normalize_paths(self) -> Settings:
        self.project_root = self.project_root.resolve()
        if not self.upload_dir.is_absolute():
            self.upload_dir = (self.project_root / self.upload_dir).resolve()
        else:
            self.upload_dir = self.upload_dir.resolve()
        if self.db_path is not None:
            if not self.db_path.is_absolute():
                self.db_path = (self.project_root / self.db_path).resolve()
            else:
                self.db_path = self.db_path.resolve()
        return self

    @property
    def resolved_db_path(self) -> Path | None:
        """Return the canonicalized absolute database path, or None if disconnected."""
        return self.db_path.resolve() if self.db_path is not None else None

    @property
    def sqlite_readonly_uri(self) -> str | None:
        """Return an RFC-compliant SQLite read-only URI safe for Windows paths with spaces."""
        resolved = self.resolved_db_path
        return f"{resolved.as_uri()}?mode=ro" if resolved is not None else None

    def secret_values(self) -> tuple[str, ...]:
        """Return active raw secret values strictly for internal redaction filters."""
        secrets: list[str] = []
        for field_value in (self.gemini_api_key, self.openai_api_key):
            if field_value is not None:
                raw = field_value.get_secret_value().strip()
                if raw:
                    secrets.append(raw)
        return tuple(secrets)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear the cached settings instance (intended for test isolation)."""
    get_settings.cache_clear()
