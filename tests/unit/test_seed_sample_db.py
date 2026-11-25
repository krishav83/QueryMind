"""Unit and integration checks for the deterministic SQLite seed script."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from data.seed_sample_db import main, seed_database


def test_seed_database_creates_expected_tables_rows_and_foreign_keys(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "sample.db"
    created_path = seed_database(db_file)

    assert created_path == db_file.resolve()
    assert created_path.exists()

    with closing(sqlite3.connect(created_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert tables == {"customers", "products", "orders", "order_items"}

        counts = {
            "customers": conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
            "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "order_items": conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],
        }
        assert counts == {
            "customers": 5,
            "products": 5,
            "orders": 6,
            "order_items": 9,
        }

        fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
        assert fk_violations == []

        line_mismatches = conn.execute(
            """
            SELECT oi.id, oi.line_total, p.unit_price * oi.quantity
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            WHERE ABS(oi.line_total - (p.unit_price * oi.quantity)) > 1e-6
            """
        ).fetchall()
        assert line_mismatches == []

        order_total_mismatches = conn.execute(
            """
            SELECT o.id, o.total_amount, SUM(oi.line_total)
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            GROUP BY o.id, o.total_amount
            HAVING ABS(o.total_amount - SUM(oi.line_total)) > 1e-6
            """
        ).fetchall()
        assert order_total_mismatches == []

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO orders (id, customer_id, order_date, status, total_amount) "
                "VALUES (999, 9999, '2025-07-01', 'pending', 10.0)"
            )


def test_seed_database_respects_overwrite_flag(tmp_path: Path) -> None:
    db_file = tmp_path / "sample.db"
    seed_database(db_file)

    with pytest.raises(FileExistsError):
        seed_database(db_file, overwrite=False)

    recreated = seed_database(db_file, overwrite=True)
    assert recreated.exists()


def test_seed_database_cli_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "cli_sample.db"
    monkeypatch.setattr(sys, "argv", ["seed_sample_db.py", "--db-path", str(db_file)])
    main()
    assert db_file.exists()

    monkeypatch.setattr(
        sys, "argv", ["seed_sample_db.py", "--db-path", str(db_file), "--overwrite"]
    )
    main()
    assert db_file.exists()
