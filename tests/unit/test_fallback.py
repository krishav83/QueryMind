"""Unit and integration tests for all 6 incremental deterministic fallback capabilities."""

from __future__ import annotations

import pytest

from querymind.db.executor import QueryExecutor
from querymind.db.inspector import SchemaInspector
from querymind.domain.exceptions import UnsupportedDeterministicQueryError
from querymind.domain.models import SchemaSnapshot
from querymind.llm.providers.fallback import DeterministicFallbackProvider
from querymind.security.validator import SQLValidator


@pytest.fixture()
def inspector_schema(seeded_db_path) -> SchemaSnapshot:
    return SchemaInspector(seeded_db_path).get_schema()


def test_fallback_capability_1_simple_select(
    seeded_db_path,
    inspector_schema: SchemaSnapshot,
) -> None:
    provider = DeterministicFallbackProvider()
    gen = provider.generate_sql("Show all customers", inspector_schema)
    assert gen.mode == "fallback"

    validated = SQLValidator().validate(
        gen.sql,
        inspector_schema,
        existing_params=gen.params,
    )
    result = QueryExecutor(seeded_db_path).execute(validated)
    assert result.row_count == 5


def test_fallback_capability_2_aggregations_count_sum_avg(
    seeded_db_path,
    inspector_schema: SchemaSnapshot,
) -> None:
    provider = DeterministicFallbackProvider()
    validator = SQLValidator()
    executor = QueryExecutor(seeded_db_path)

    # COUNT
    q_count = provider.generate_sql("How many orders are there?", inspector_schema)
    res_count = executor.execute(
        validator.validate(q_count.sql, inspector_schema, existing_params=q_count.params)
    )
    assert res_count.rows[0]["metric_value"] == 6

    # SUM
    q_sum = provider.generate_sql("What is the total total_amount of orders?", inspector_schema)
    res_sum = executor.execute(
        validator.validate(q_sum.sql, inspector_schema, existing_params=q_sum.params)
    )
    assert res_sum.rows[0]["metric_value"] == pytest.approx(2071.5)

    # AVG
    q_avg = provider.generate_sql("What is the average unit_price of products?", inspector_schema)
    res_avg = executor.execute(
        validator.validate(q_avg.sql, inspector_schema, existing_params=q_avg.params)
    )
    assert res_avg.rows[0]["metric_value"] == pytest.approx(225.1)


def test_fallback_capability_3_where_uses_bound_parameters(
    seeded_db_path,
    inspector_schema: SchemaSnapshot,
) -> None:
    provider = DeterministicFallbackProvider()
    gen = provider.generate_sql(
        "Show orders with total_amount > 300",
        inspector_schema,
    )
    assert "300" not in gen.sql
    assert gen.params == {"p_0": 300}

    validated = SQLValidator().validate(gen.sql, inspector_schema, existing_params=gen.params)
    res = QueryExecutor(seeded_db_path).execute(validated)
    assert res.row_count == 3

    gen_status = provider.generate_sql("List completed orders", inspector_schema)
    assert "completed" not in gen_status.sql
    assert gen_status.params == {"p_0": "completed"}


def test_fallback_capability_4_order_by_and_limit(
    seeded_db_path,
    inspector_schema: SchemaSnapshot,
) -> None:
    provider = DeterministicFallbackProvider()
    gen = provider.generate_sql("Top 2 products by unit_price", inspector_schema)
    validated = SQLValidator().validate(gen.sql, inspector_schema, existing_params=gen.params)
    res = QueryExecutor(seeded_db_path).execute(validated)

    assert validated.applied_limit == 2
    assert res.row_count == 2
    assert res.rows[0]["name"] == "Cloud Warehouse Workshop"


def test_fallback_capability_5_group_by(
    seeded_db_path,
    inspector_schema: SchemaSnapshot,
) -> None:
    provider = DeterministicFallbackProvider()
    gen = provider.generate_sql("Count orders by status", inspector_schema)
    validated = SQLValidator().validate(gen.sql, inspector_schema, existing_params=gen.params)
    res = QueryExecutor(seeded_db_path).execute(validated)

    counts = {row["status"]: row["metric_value"] for row in res.rows}
    assert counts == {"completed": 4, "pending": 1, "cancelled": 1}


def test_fallback_capability_6_fk_joins_and_rejects_unjoined_tables(
    seeded_db_path,
    inspector_schema: SchemaSnapshot,
) -> None:
    provider = DeterministicFallbackProvider()
    gen = provider.generate_sql(
        "Show total total_amount of orders per customer country",
        inspector_schema,
    )
    assert "JOIN" in gen.sql.upper()

    validated = SQLValidator().validate(gen.sql, inspector_schema, existing_params=gen.params)
    res = QueryExecutor(seeded_db_path).execute(validated)
    assert len(res.rows) == 5

    # Customers and products have no direct FK -> must fail explicitly rather than cross-join
    with pytest.raises(UnsupportedDeterministicQueryError):
        provider.generate_sql("Show customers and products", inspector_schema)


@pytest.mark.parametrize(
    "unsupported_question",
    [
        "",
        "What is the weather today?",
        "Drop table orders",
        "Show customers using a window rank() over partition",
        "List customers, products, orders, and order_items",
    ],
)
def test_fallback_rejects_unsupported_queries_gracefully(
    inspector_schema: SchemaSnapshot,
    unsupported_question: str,
) -> None:
    provider = DeterministicFallbackProvider()
    with pytest.raises(UnsupportedDeterministicQueryError):
        provider.generate_sql(unsupported_question, inspector_schema)
