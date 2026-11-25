"""Deterministic SQLGlot AST-based SQL explainer (zero row data required)."""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp

from querymind.domain.models import SchemaSnapshot
from querymind.llm.base import BaseLLMProvider

_logger = logging.getLogger("querymind.explainer")


class SQLExplainer:
    """Produces human-readable explanations of validated SQL queries."""

    def explain(
        self,
        sql: str,
        schema: SchemaSnapshot,
        provider: BaseLLMProvider | None = None,
    ) -> str:
        """Explain ``sql`` via LLM provider when active, falling back to AST explanation."""
        if provider is not None and provider.mode == "llm":
            try:
                llm_text = provider.explain_sql(sql, schema).strip()
                if llm_text:
                    return llm_text
            except Exception:
                _logger.warning("LLM SQL explanation failed; using AST explanation fallback.")

        return self.explain_from_ast(sql, schema)

    def explain_from_ast(self, sql: str, schema: SchemaSnapshot) -> str:
        """Generate a structured, clause-by-clause explanation using SQLGlot AST."""
        del schema  # Reserved for optional metadata enrichment without reading rows.
        try:
            tree = sqlglot.parse_one(sql, read="sqlite")
        except sqlglot.errors.ParseError:
            return f"Executes the validated read-only SQL query: {sql}"

        parts: list[str] = []

        with_clause = tree.args.get("with_") or tree.args.get("with")
        if isinstance(with_clause, exp.With):
            cte_names = [cte.alias_or_name for cte in with_clause.expressions if cte.alias_or_name]
            if cte_names:
                parts.append(f"Defines temporary CTE(s): {', '.join(cte_names)}.")

        if isinstance(tree, exp.Union):
            parts.append("Combines results from multiple SELECT queries using a set operation.")
        elif isinstance(tree, exp.Select):
            projections = [expr.sql(dialect="sqlite") for expr in tree.expressions]
            if projections:
                parts.append(f"Selects {', '.join(projections)}.")

            from_clause = tree.args.get("from_") or tree.args.get("from")
            if isinstance(from_clause, exp.From) and from_clause.this is not None:
                parts.append(f"Reads from {from_clause.this.sql(dialect='sqlite')}.")

            joins = tree.args.get("joins") or []
            if joins:
                join_desc = [j.sql(dialect="sqlite") for j in joins]
                parts.append(f"Joins related data via: {'; '.join(join_desc)}.")

            where_clause = tree.args.get("where")
            if isinstance(where_clause, exp.Where) and where_clause.this is not None:
                parts.append(f"Filters rows where {where_clause.this.sql(dialect='sqlite')}.")

            group_clause = tree.args.get("group")
            if isinstance(group_clause, exp.Group) and group_clause.expressions:
                group_cols = [g.sql(dialect="sqlite") for g in group_clause.expressions]
                parts.append(f"Groups results by {', '.join(group_cols)}.")

            having_clause = tree.args.get("having")
            if isinstance(having_clause, exp.Having) and having_clause.this is not None:
                parts.append(f"Filters groups where {having_clause.this.sql(dialect='sqlite')}.")

            order_clause = tree.args.get("order")
            if isinstance(order_clause, exp.Order) and order_clause.expressions:
                order_cols = [o.sql(dialect="sqlite") for o in order_clause.expressions]
                parts.append(f"Sorts results by {', '.join(order_cols)}.")

        limit_clause = tree.args.get("limit")
        if isinstance(limit_clause, exp.Limit) and limit_clause.expression is not None:
            limit_sql = limit_clause.expression.sql(dialect="sqlite")
            parts.append(f"Limits output to at most {limit_sql} row(s).")

        return " ".join(parts) if parts else f"Executes validated read-only SQL: {sql}"
