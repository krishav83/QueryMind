"""Unit tests for restricted read-only QueryExecutor."""

from __future__ import annotations

from pathlib import Path

import pytest

from querymind.db.executor import QueryExecutor
from querymind.domain.exceptions import QueryExecutionError, QueryTimeoutError
from querymind.domain.models import ValidatedQuery


def test_executor_requires_validated_query_instance(seeded_db_path: Path) -> None:
    executor = QueryExecutor(seeded_db_path)
    with pytest.raises(TypeError, match="ValidatedQuery"):
        executor.execute("SELECT * FROM customers")  # type: ignore[arg-type]


def test_executor_runs_parameterized_analytical_query(seeded_db_path: Path) -> None:
    executor = QueryExecutor(seeded_db_path, max_rows=100, timeout_seconds=5.0)
    query = ValidatedQuery(
        sql=(
            "SELECT UPPER(c.country) AS country, COUNT(o.id) AS order_count, "
            "ROUND(SUM(o.total_amount), 2) AS total_sales "
            "FROM customers c "
            "JOIN orders o ON o.customer_id = c.id "
            "WHERE o.status = :status "
            "GROUP BY c.country "
            "ORDER BY total_sales DESC "
            "LIMIT 50"
        ),
        params={"status": "completed"},
        referenced_tables=("customers", "orders"),
        applied_limit=50,
    )

    result = executor.execute(query)

    assert result.columns == ["country", "order_count", "total_sales"]
    assert result.truncated is False
    assert result.row_count == 4
    assert result.rows[0]["country"] == "GERMANY"
    assert result.rows[0]["total_sales"] == 598.0
    assert result.execution_time_ms >= 0.0


def test_executor_enforces_row_cap_and_sets_truncated_flag(seeded_db_path: Path) -> None:
    executor = QueryExecutor(seeded_db_path, max_rows=2)
    query = ValidatedQuery(
        sql="SELECT id, name FROM customers ORDER BY id LIMIT 100",
        params={},
        referenced_tables=("customers",),
        applied_limit=100,
    )

    result = executor.execute(query)

    assert result.row_count == 2
    assert result.truncated is True
    assert [row["id"] for row in result.rows] == [1, 2]


def test_executor_handles_null_values_cleanly(seeded_db_path: Path) -> None:
    executor = QueryExecutor(seeded_db_path)
    query = ValidatedQuery(
        sql="SELECT NULLIF(status, 'pending') AS cleaned_status FROM orders ORDER BY id LIMIT 6",
        params={},
        referenced_tables=("orders",),
        applied_limit=6,
    )

    result = executor.execute(query)
    assert result.rows[2]["cleaned_status"] is None


def test_executor_aborts_runaway_query_on_timeout(seeded_db_path: Path) -> None:
    # 9-way cross join over order_items (9^9 = 387M rows) guaranteed to hit tiny timeout.
    executor = QueryExecutor(seeded_db_path, timeout_seconds=0.01)
    query = ValidatedQuery(
        sql=(
            "SELECT COUNT(*) AS total FROM "
            "order_items a, order_items b, order_items c, "
            "order_items d, order_items e, order_items f, "
            "order_items g, order_items h, order_items i "
            "LIMIT 1"
        ),
        params={},
        referenced_tables=("order_items",),
        applied_limit=1,
    )

    with pytest.raises(QueryTimeoutError):
        executor.execute(query)


def test_executor_rejects_disallowed_table_or_unsafe_statement(seeded_db_path: Path) -> None:
    executor = QueryExecutor(seeded_db_path, allowed_tables=("customers",))
    query = ValidatedQuery(
        sql="SELECT id FROM orders LIMIT 5",
        params={},
        referenced_tables=("orders",),
        applied_limit=5,
    )

    with pytest.raises(QueryExecutionError):
        executor.execute(query)
