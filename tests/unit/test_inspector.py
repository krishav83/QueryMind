"""Unit tests for automatic SQLite schema discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from querymind.db.inspector import SchemaInspector
from querymind.domain.exceptions import DatabaseConnectionError


def test_schema_inspector_discovers_tables_columns_pks_and_fks(seeded_db_path: Path) -> None:
    inspector = SchemaInspector(seeded_db_path)
    snapshot = inspector.get_schema()

    assert snapshot.dialect == "sqlite"
    assert snapshot.table_names() == ("customers", "order_items", "orders", "products")

    orders = snapshot.get_table("orders")
    assert orders is not None
    assert orders.column_names() == ("id", "customer_id", "order_date", "status", "total_amount")
    id_col = next(c for c in orders.columns if c.name == "id")
    assert id_col.primary_key is True
    assert len(orders.foreign_keys) == 1
    assert orders.foreign_keys[0].column == "customer_id"
    assert orders.foreign_keys[0].referred_table == "customers"
    assert orders.foreign_keys[0].referred_column == "id"

    order_items = snapshot.get_table("order_items")
    assert order_items is not None
    assert len(order_items.foreign_keys) == 2


def test_schema_inspector_caches_and_refreshes_snapshot(seeded_db_path: Path) -> None:
    inspector = SchemaInspector(seeded_db_path)
    first = inspector.get_schema()
    second = inspector.get_schema()
    refreshed = inspector.get_schema(force_refresh=True)

    assert first is second
    assert refreshed is not first
    assert refreshed == first


def test_schema_inspector_raises_on_missing_database(tmp_path: Path) -> None:
    inspector = SchemaInspector(tmp_path / "missing.db")
    with pytest.raises(DatabaseConnectionError):
        inspector.get_schema()
