"""Integration tests for trusted schema discovery paired with restricted execution."""

from __future__ import annotations

from querymind.db.executor import QueryExecutor
from querymind.db.inspector import SchemaInspector
from querymind.domain.models import ValidatedQuery


def test_inspector_and_executor_work_together_on_seeded_db(seeded_db_path) -> None:
    inspector = SchemaInspector(seeded_db_path)
    schema = inspector.get_schema()

    assert set(schema.table_names()) == {"customers", "order_items", "orders", "products"}

    executor = QueryExecutor(
        seeded_db_path,
        max_rows=50,
        timeout_seconds=5.0,
        allowed_tables=schema.table_names(),
    )
    query = ValidatedQuery(
        sql=(
            "SELECT c.name, o.total_amount "
            "FROM orders o JOIN customers c ON o.customer_id = c.id "
            "WHERE o.id = :order_id LIMIT 10"
        ),
        params={"order_id": 2},
        referenced_tables=("orders", "customers"),
        applied_limit=10,
    )

    result = executor.execute(query)
    assert result.row_count == 1
    assert result.rows == [{"name": "Maya Lin", "total_amount": 198.5}]
