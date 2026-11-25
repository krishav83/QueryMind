"""Unit tests for domain models, immutability, and bind-parameter contracts."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from pydantic import ValidationError

from querymind.domain.models import (
    ColumnInfo,
    ExecutionResult,
    ForeignKeyInfo,
    GeneratedSQL,
    QueryRequest,
    QueryResponse,
    SchemaSnapshot,
    TableInfo,
    ValidatedQuery,
)


def test_schema_snapshot_lookup_and_metadata_only_prompt_summary() -> None:
    snapshot = SchemaSnapshot(
        dialect="sqlite",
        tables=(
            TableInfo(
                name="customers",
                columns=(
                    ColumnInfo(name="id", data_type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="name", data_type="TEXT", nullable=False),
                ),
            ),
            TableInfo(
                name="orders",
                columns=(
                    ColumnInfo(name="id", data_type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="customer_id", data_type="INTEGER", nullable=False),
                ),
                foreign_keys=(
                    ForeignKeyInfo(
                        column="customer_id",
                        referred_table="customers",
                        referred_column="id",
                    ),
                ),
            ),
        ),
    )

    assert snapshot.table_names() == ("customers", "orders")
    assert snapshot.get_table("CUSTOMERS") is not None
    assert snapshot.get_table("orders").column_names() == ("id", "customer_id")  # type: ignore[union-attr]
    assert snapshot.get_table("nonexistent") is None

    summary = snapshot.to_prompt_summary()
    assert "Dialect: sqlite" in summary
    assert "customers: id (INTEGER, PK, NOT NULL), name (TEXT, NOT NULL)" in summary
    assert "FK: orders.customer_id -> customers.id" in summary


def test_validated_query_params_are_immutable_after_creation() -> None:
    source_params = {"status": "completed", "min_total": 100.0}
    query = ValidatedQuery(
        sql=(
            "SELECT id, total_amount FROM orders "
            "WHERE status = :status AND total_amount > :min_total LIMIT 100"
        ),
        params=source_params,
        referenced_tables=("orders",),
        applied_limit=100,
    )

    # Mutating the caller's dict must not affect the validated query.
    source_params["status"] = "cancelled"
    assert query.params["status"] == "completed"
    assert isinstance(query.params, MappingProxyType)

    with pytest.raises(ValidationError):
        query.sql = "SELECT 1"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        query.params = {"status": "other"}  # type: ignore[misc]

    with pytest.raises(TypeError):
        query.params["status"] = "hacked"  # type: ignore[index]

    assert query.model_dump()["params"] == {"status": "completed", "min_total": 100.0}


def test_validated_query_enforces_exact_placeholder_parameter_match() -> None:
    # Missing parameter
    with pytest.raises(ValidationError, match="missing parameters: min_total"):
        ValidatedQuery(
            sql=(
                "SELECT id FROM orders "
                "WHERE status = :status AND total_amount > :min_total LIMIT 10"
            ),
            params={"status": "completed"},
            applied_limit=10,
        )

    # Unexpected parameter
    with pytest.raises(ValidationError, match="unexpected parameters: extra"):
        ValidatedQuery(
            sql="SELECT id FROM orders WHERE status = :status LIMIT 10",
            params={"status": "completed", "extra": 42},
            applied_limit=10,
        )


def test_validated_query_ignores_colons_inside_quoted_literals_identifiers_and_comments() -> None:
    query = ValidatedQuery(
        sql=(
            'SELECT id, "col:quoted", `col:tick`, [col:bracket], total_amount::text '
            "FROM orders "
            "-- comment with :ignored_comment_param\n"
            "/* block comment :ignored_block */\n"
            "WHERE created_at >= '2025-01-01 12:30:00' "
            "AND note != 'it''s :not_a_param' "
            "AND status = :status LIMIT 50"
        ),
        params={"status": "completed"},
        applied_limit=50,
    )

    assert dict(query.params) == {"status": "completed"}


def test_validated_query_supports_repeated_named_placeholders_and_scalar_types() -> None:
    query = ValidatedQuery(
        sql=(
            "SELECT id FROM orders "
            "WHERE (status = :target OR segment = :target) "
            "AND in_stock = :flag AND note IS :note LIMIT 20"
        ),
        params={"target": "completed", "flag": True, "note": None},
        applied_limit=20,
    )

    assert dict(query.params) == {"target": "completed", "flag": True, "note": None}


def test_validated_query_accepts_explicit_none_params_and_rejects_non_mapping() -> None:
    query = ValidatedQuery(
        sql="SELECT id FROM orders LIMIT 10",
        params=None,  # type: ignore[arg-type]
        applied_limit=10,
    )
    assert dict(query.params) == {}

    with pytest.raises(ValidationError):
        ValidatedQuery(
            sql="SELECT id FROM orders LIMIT 10",
            params=["not_a_mapping"],  # type: ignore[arg-type]
            applied_limit=10,
        )


@pytest.mark.parametrize(
    "bad_sql",
    [
        "SELECT id FROM orders WHERE status = ? LIMIT 10",
        "SELECT id FROM orders WHERE status = ?1 LIMIT 10",
        "SELECT id FROM orders WHERE status = $1 LIMIT 10",
        "SELECT id FROM orders WHERE status = $status LIMIT 10",
        "SELECT id FROM orders WHERE status = @status LIMIT 10",
        "SELECT id FROM orders WHERE status = @1 LIMIT 10",
        "SELECT id FROM orders WHERE status = :1 LIMIT 10",
        "   ",
    ],
)
def test_validated_query_rejects_disallowed_bind_styles_and_blank_sql(bad_sql: str) -> None:
    with pytest.raises(ValidationError):
        ValidatedQuery(sql=bad_sql, params={}, applied_limit=10)


def test_validated_query_rejects_invalid_param_names_and_non_scalar_values() -> None:
    with pytest.raises(ValidationError):
        ValidatedQuery(
            sql="SELECT id FROM orders WHERE status = :bad_key LIMIT 10",
            params={"1invalid": "completed"},
            applied_limit=10,
        )

    with pytest.raises(ValidationError):
        ValidatedQuery(
            sql="SELECT id FROM orders WHERE status = :status LIMIT 10",
            params={"status": ["completed"]},  # type: ignore[dict-item]
            applied_limit=10,
        )

    with pytest.raises(ValidationError):
        ValidatedQuery(
            sql="SELECT id FROM orders WHERE total_amount = :amount LIMIT 10",
            params={"amount": float("nan")},
            applied_limit=10,
        )


def test_query_request_generated_sql_and_response_models() -> None:
    req = QueryRequest(question="  Show top 5 orders  ", max_rows=5000)
    assert req.question == "Show top 5 orders"
    assert req.max_rows == 5000

    with pytest.raises(ValidationError):
        QueryRequest(question="   ")

    with pytest.raises(ValidationError):
        QueryRequest(question="x" * 1001)

    with pytest.raises(ValidationError):
        QueryRequest(question="Show orders", max_rows=0)

    with pytest.raises(ValidationError):
        QueryRequest(question="Show orders", max_rows=5001)

    gen = GeneratedSQL(
        sql="SELECT id FROM orders LIMIT 5",
        provider_name="fallback",
        mode="fallback",
    )
    assert gen.params == {}

    res = ExecutionResult(
        columns=["id"],
        rows=[{"id": 1}],
        row_count=1,
        truncated=False,
        execution_time_ms=1.2,
    )
    assert res.row_count == 1

    with pytest.raises(ValidationError):
        ExecutionResult(columns=[], rows=[], row_count=-1, execution_time_ms=0.0)

    resp = QueryResponse(
        question="Show orders",
        sql="SELECT id FROM orders LIMIT 5",
        explanation="Lists order IDs.",
        provider_mode="fallback",
        provider_name="deterministic_fallback",
        columns=["id"],
        rows=[{"id": 1}],
        row_count=1,
        truncated=False,
        execution_time_ms=1.2,
    )
    assert resp.provider_mode == "fallback"
