"""Typed domain and security exceptions for QueryMind."""

from __future__ import annotations


class QueryMindError(Exception):
    """Base exception carrying a safe user-facing message and machine-readable code."""

    default_code = "querymind_error"

    def __init__(
        self,
        message: str,
        *,
        user_message: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or message
        self.code = code or self.default_code


class ConfigurationError(QueryMindError):
    """Raised when runtime configuration or paths are invalid."""

    default_code = "configuration_error"


class DatabaseConnectionError(QueryMindError):
    """Raised when a database connection cannot be established safely."""

    default_code = "database_connection_error"


class NoDatabaseConnectedError(DatabaseConnectionError):
    """Raised when an operation requires a connected database but none is active."""

    default_code = "no_database_connected"


class DatabaseUploadError(QueryMindError):
    """Raised when an uploaded SQLite database fails validation."""

    default_code = "database_upload_error"


class DatabaseUploadTooLargeError(DatabaseUploadError):
    """Raised when an uploaded database exceeds the configured size limit."""

    default_code = "database_upload_too_large"


class SchemaDiscoveryError(QueryMindError):
    """Raised when automatic schema inspection fails."""

    default_code = "schema_discovery_error"


class SQLValidationError(QueryMindError):
    """Base exception for SQL AST or parameter validation failures."""

    default_code = "sql_validation_error"


class UnsafeSQLError(SQLValidationError):
    """Raised when SQL contains disallowed statements, functions, or system tables."""

    default_code = "unsafe_sql"


class UnknownTableError(SQLValidationError):
    """Raised when SQL references a table not present in the discovered schema."""

    default_code = "unknown_table"


class UnknownColumnError(SQLValidationError):
    """Raised when SQL references an unknown column in its active scope."""

    default_code = "unknown_column"


class AmbiguousColumnError(SQLValidationError):
    """Raised when an unqualified column exists in multiple joined sources."""

    default_code = "ambiguous_column"


class UnsupportedSQLError(SQLValidationError):
    """Raised when SQL syntax is unsupported or fails closed during parsing."""

    default_code = "unsupported_sql"


class QueryExecutionError(QueryMindError):
    """Raised when read-only SQL execution fails."""

    default_code = "query_execution_error"


class QueryTimeoutError(QueryExecutionError):
    """Raised when a query exceeds the configured execution timeout."""

    default_code = "query_timeout"


class LLMProviderError(QueryMindError):
    """Raised when an LLM provider fails or returns an invalid response."""

    default_code = "llm_provider_error"


class UnsupportedDeterministicQueryError(LLMProviderError):
    """Raised when a question falls outside supported deterministic fallback patterns."""

    default_code = "unsupported_deterministic_query"
