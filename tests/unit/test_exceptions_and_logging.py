"""Unit tests for typed domain exceptions and logging configuration."""

from __future__ import annotations

import logging

from querymind.domain.exceptions import (
    AmbiguousColumnError,
    ConfigurationError,
    DatabaseConnectionError,
    LLMProviderError,
    QueryExecutionError,
    QueryMindError,
    QueryTimeoutError,
    SchemaDiscoveryError,
    SQLValidationError,
    UnknownColumnError,
    UnknownTableError,
    UnsafeSQLError,
    UnsupportedDeterministicQueryError,
    UnsupportedSQLError,
)
from querymind.logging_config import SecretRedactingFilter, configure_logging


def test_exception_hierarchy_defaults_and_custom_user_message() -> None:
    err = UnsafeSQLError("AST detected DROP", user_message="Only read-only queries are allowed.")
    assert isinstance(err, SQLValidationError)
    assert isinstance(err, QueryMindError)
    assert err.code == "unsafe_sql"
    assert err.user_message == "Only read-only queries are allowed."

    fallback_msg_err = QueryTimeoutError("Timed out after 5s")
    assert isinstance(fallback_msg_err, QueryExecutionError)
    assert fallback_msg_err.code == "query_timeout"
    assert fallback_msg_err.user_message == "Timed out after 5s"

    assert ConfigurationError("bad").code == "configuration_error"
    assert DatabaseConnectionError("bad").code == "database_connection_error"
    assert SchemaDiscoveryError("bad").code == "schema_discovery_error"
    assert UnknownTableError("bad").code == "unknown_table"
    assert UnknownColumnError("bad").code == "unknown_column"
    assert AmbiguousColumnError("bad").code == "ambiguous_column"
    assert UnsupportedSQLError("bad").code == "unsupported_sql"
    assert LLMProviderError("bad").code == "llm_provider_error"
    assert UnsupportedDeterministicQueryError("bad").code == "unsupported_deterministic_query"


def test_configure_logging_is_idempotent_and_filter_updates_secrets() -> None:
    logger = configure_logging(level="WARNING", secrets=("old-secret",), logger_name="querymind.u1")
    logger = configure_logging(level="INFO", secrets=("new-secret",), logger_name="querymind.u1")

    assert len(logger.handlers) == 1
    assert logger.level == logging.INFO
    assert logger.propagate is False

    redacting_filter = SecretRedactingFilter(("first-secret",))
    redacting_filter.update_secrets(("second-secret",))
    record = logging.LogRecord(
        name="querymind.u1",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Attempt with %s",
        args=("second-secret",),
        exc_info=None,
    )
    assert redacting_filter.filter(record) is True
    assert "second-secret" not in str(record.msg)
    assert "[REDACTED]" in str(record.msg)

    # Unformattable msg/args falls back gracefully while still redacting secrets.
    bad_format_record = logging.LogRecord(
        name="querymind.u1",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Attempt with second-secret %s %s",
        args=("only-one-arg",),
        exc_info=None,
    )
    assert redacting_filter.filter(bad_format_record) is True
    assert "second-secret" not in str(bad_format_record.msg)
    assert "[REDACTED]" in str(bad_format_record.msg)
