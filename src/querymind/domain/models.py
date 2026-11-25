"""Core Pydantic domain models for QueryMind."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

ScalarParam = str | int | float | bool | None

_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NAMED_PLACEHOLDER_RE = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")
_NUMERIC_COLON_PLACEHOLDER_RE = re.compile(r"(?<![:\w]):[0-9]+")
_SQL_COMMENTS_AND_QUOTES_RE = re.compile(
    r"""
    --[^\r\n]*               # single-line comment
    | /\*[\s\S]*?\*/         # block comment
    | '(?:''|[^'])*'         # single-quoted string literal
    | "(?:""|[^"])*"         # double-quoted identifier
    | `(?:``|[^`])*`         # backtick-quoted identifier
    | \[[^\]]*\]             # bracket-quoted identifier
    """,
    re.VERBOSE,
)


def _strip_sql_literals_and_comments(sql: str) -> str:
    """Replace quoted literals, quoted identifiers, and comments with spaces."""
    return _SQL_COMMENTS_AND_QUOTES_RE.sub(" ", sql)


def extract_named_placeholders(sql: str) -> set[str]:
    """Extract named bind placeholders (``:name``) and reject unsupported bind styles."""
    stripped = _strip_sql_literals_and_comments(sql)
    if "?" in stripped:
        raise ValueError("Positional '?' placeholders are not permitted; use named ':name' binds.")
    if "$" in stripped:
        raise ValueError("Dollar '$name'/'$1' placeholders are not permitted; use ':name' binds.")
    if "@" in stripped:
        raise ValueError("SQLite '@name' placeholders are not permitted; use named ':name' binds.")
    if _NUMERIC_COLON_PLACEHOLDER_RE.search(stripped):
        raise ValueError("Numeric ':1' placeholders are not permitted; use named ':name' binds.")
    return set(_NAMED_PLACEHOLDER_RE.findall(stripped))


def _coerce_immutable_params(value: object) -> MappingProxyType[str, ScalarParam]:
    """Validate parameter names/types and freeze the mapping into a MappingProxyType."""
    if not isinstance(value, Mapping):
        raise ValueError("Query parameters must be a mapping.")

    cleaned: dict[str, ScalarParam] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _PARAM_NAME_RE.match(key):
            raise ValueError(f"Invalid parameter name: {key!r}")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(
                f"Parameter {key!r} has unsupported type {type(item).__name__}; "
                "only scalar values (str, int, float, bool, None) are allowed."
            )
        if isinstance(item, float) and (math.isnan(item) or math.isinf(item)):
            raise ValueError(f"Parameter {key!r} cannot be NaN or infinity.")
        cleaned[key] = item

    return MappingProxyType(cleaned)


ImmutableParams = Annotated[
    MappingProxyType[str, ScalarParam],
    PlainSerializer(lambda v: dict(v), return_type=dict[str, Any]),
]


class ColumnInfo(BaseModel):
    """Metadata describing a single table column."""

    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False


class ForeignKeyInfo(BaseModel):
    """Metadata describing a foreign-key relationship from one column to another."""

    model_config = ConfigDict(frozen=True)

    column: str
    referred_table: str
    referred_column: str


class TableInfo(BaseModel):
    """Metadata describing a database table."""

    model_config = ConfigDict(frozen=True)

    name: str
    columns: tuple[ColumnInfo, ...]
    foreign_keys: tuple[ForeignKeyInfo, ...] = ()

    def column_names(self) -> tuple[str, ...]:
        """Return ordered column names for this table."""
        return tuple(col.name for col in self.columns)


class SchemaSnapshot(BaseModel):
    """Dialect-agnostic snapshot of discovered database schema metadata."""

    model_config = ConfigDict(frozen=True)

    dialect: str = "sqlite"
    tables: tuple[TableInfo, ...] = ()

    def table_names(self) -> tuple[str, ...]:
        """Return all discovered table names."""
        return tuple(table.name for table in self.tables)

    def get_table(self, name: str) -> TableInfo | None:
        """Look up a table case-insensitively by name."""
        target = name.lower()
        for table in self.tables:
            if table.name.lower() == target:
                return table
        return None

    def to_prompt_summary(self) -> str:
        """Render structural schema metadata (never row values) for LLM prompts."""
        lines: list[str] = [f"Dialect: {self.dialect}", "Tables:"]
        for table in self.tables:
            col_parts = []
            for col in table.columns:
                pk_flag = ", PK" if col.primary_key else ""
                null_flag = "" if col.nullable else ", NOT NULL"
                col_parts.append(f"{col.name} ({col.data_type}{pk_flag}{null_flag})")
            lines.append(f"- {table.name}: {', '.join(col_parts)}")
            for fk in table.foreign_keys:
                lines.append(
                    f"  FK: {table.name}.{fk.column} -> {fk.referred_table}.{fk.referred_column}"
                )
        return "\n".join(lines)


class GeneratedSQL(BaseModel):
    """Unvalidated SQL candidate produced by an LLM provider or deterministic fallback."""

    model_config = ConfigDict(frozen=True)

    sql: str = Field(min_length=1)
    params: dict[str, ScalarParam] = Field(default_factory=dict)
    provider_name: str = Field(min_length=1)
    mode: Literal["fallback", "llm"]
    raw_explanation: str | None = None


class ValidatedQuery(BaseModel):
    """Immutable SQL + bound parameter contract produced exclusively by SQL validation."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    sql: str = Field(min_length=1)
    params: ImmutableParams = Field(default_factory=lambda: MappingProxyType({}))
    referenced_tables: tuple[str, ...] = ()
    applied_limit: int = Field(ge=1)

    @field_validator("sql")
    @classmethod
    def _validate_sql_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Validated SQL cannot be empty.")
        return cleaned

    @field_validator("params", mode="before")
    @classmethod
    def _freeze_params(cls, value: object) -> MappingProxyType[str, ScalarParam]:
        if value is None:
            return MappingProxyType({})
        return _coerce_immutable_params(value)

    @model_validator(mode="after")
    def _verify_placeholders_match_params(self) -> ValidatedQuery:
        placeholders = extract_named_placeholders(self.sql)
        param_keys = set(self.params.keys())

        missing = sorted(placeholders - param_keys)
        unexpected = sorted(param_keys - placeholders)

        if missing or unexpected:
            details: list[str] = []
            if missing:
                details.append(f"missing parameters: {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected parameters: {', '.join(unexpected)}")
            raise ValueError(
                "SQL bind placeholders and parameter mapping must match exactly "
                f"({'; '.join(details)})."
            )
        return self


class ExecutionResult(BaseModel):
    """Tabular result returned by the read-only query executor."""

    model_config = ConfigDict(frozen=True)

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)
    truncated: bool = False
    execution_time_ms: float = Field(ge=0.0)


class QueryRequest(BaseModel):
    """API request payload for natural-language SQL querying."""

    question: str = Field(min_length=1, max_length=1000)
    max_rows: int | None = Field(default=None, ge=1, le=5000)

    @field_validator("question")
    @classmethod
    def _clean_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Question cannot be blank.")
        return cleaned


class QueryResponse(BaseModel):
    """API response payload containing validated SQL, explanation, and query results."""

    question: str
    sql: str
    explanation: str
    provider_mode: Literal["fallback", "llm"]
    provider_name: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)
    truncated: bool = False
    execution_time_ms: float = Field(ge=0.0)
