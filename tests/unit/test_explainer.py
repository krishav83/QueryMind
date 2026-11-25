"""Unit tests for SQLGlot AST-based SQL explainer."""

from __future__ import annotations

from querymind.domain.models import SchemaSnapshot
from querymind.explainer.explainer import SQLExplainer


def test_ast_explainer_describes_clauses_without_row_access() -> None:
    explainer = SQLExplainer()
    schema = SchemaSnapshot(dialect="sqlite", tables=())

    sql = (
        "WITH recent AS (SELECT id, customer_id, total_amount FROM orders) "
        "SELECT c.name, SUM(r.total_amount) AS total_spent "
        "FROM recent r JOIN customers c ON r.customer_id = c.id "
        "WHERE r.total_amount > :p_0 "
        "GROUP BY c.name HAVING total_spent > :p_1 "
        "ORDER BY total_spent DESC LIMIT 10"
    )
    text = explainer.explain_from_ast(sql, schema)

    assert "CTE" in text
    assert "Selects" in text
    assert "Joins" in text
    assert "Filters rows" in text
    assert "Groups results" in text
    assert "Filters groups" in text
    assert "Sorts results" in text
    assert "10 row(s)" in text
