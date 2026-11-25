"""Structured logging setup with automatic secret redaction."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable

from querymind.security.sanitizer import redact_secrets


class SecretRedactingFilter(logging.Filter):
    """Filter that scrubs configured secrets from log messages before formatting."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: tuple[str, ...] = tuple(secrets)

    def update_secrets(self, secrets: Iterable[str]) -> None:
        """Replace the active secret list used for redaction."""
        self._secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact_secrets(rendered, self._secrets)
        record.args = None
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text, self._secrets)
        return True


class RedactingFormatter(logging.Formatter):
    """Formatter that ensures formatted lines and tracebacks contain no secrets."""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        secrets: Iterable[str] = (),
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._secrets: tuple[str, ...] = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_secrets(formatted, self._secrets)


def configure_logging(
    level: str = "INFO",
    secrets: Iterable[str] = (),
    logger_name: str = "querymind",
) -> logging.Logger:
    """Configure a logger with secret redaction and clean Windows-compatible console output."""
    secret_tuple = tuple(secrets)
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logger.level)
    handler.addFilter(SecretRedactingFilter(secret_tuple))
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            secrets=secret_tuple,
        )
    )
    logger.addHandler(handler)
    return logger
