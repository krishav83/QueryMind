"""Incremental deterministic NL-to-SQL fallback engine (zero API key required)."""

from __future__ import annotations

import re
from typing import Any

from sqlglot import exp

from querymind.domain.exceptions import UnsupportedDeterministicQueryError
from querymind.domain.models import ForeignKeyInfo, GeneratedSQL, SchemaSnapshot, TableInfo
from querymind.explainer.explainer import SQLExplainer

_SUPPORTED_HINT = (
    "Deterministic mode supports single-table or FK-joined queries with: "
    "SELECT columns, COUNT/SUM/AVG, WHERE filters (=, >, <, >=, <=), "
    "GROUP BY, ORDER BY, and LIMIT."
)

_UNSUPPORTED_KEYWORDS: tuple[str, ...] = (
    "drop ",
    "delete ",
    "insert ",
    "update ",
    "alter ",
    "create ",
    "truncate ",
    "pragma ",
    "attach ",
    "subquery",
    "window ",
    "over (",
    "rank(",
    "dense_rank",
    "row_number",
    "having ",
    "union ",
)

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "all",
        "and",
        "are",
        "as",
        "average",
        "avg",
        "by",
        "count",
        "display",
        "each",
        "find",
        "for",
        "from",
        "get",
        "give",
        "highest",
        "how",
        "in",
        "is",
        "list",
        "lowest",
        "many",
        "me",
        "mean",
        "most",
        "number",
        "of",
        "order",
        "per",
        "records",
        "rows",
        "select",
        "show",
        "sort",
        "sorted",
        "sum",
        "the",
        "top",
        "total",
        "what",
        "where",
        "with",
    }
)


class DeterministicFallbackProvider:
    """Rule-based, schema-aware NL-to-SQL engine using SQLGlot AST construction."""

    name = "deterministic_fallback"
    mode = "fallback"

    def __init__(self, *, default_limit: int = 500) -> None:
        self._default_limit = default_limit
        self._explainer = SQLExplainer()

    def generate_sql(self, question: str, schema: SchemaSnapshot) -> GeneratedSQL:
        """Translate supported natural-language patterns into parameterized SQL."""
        cleaned = question.strip()
        lowered = cleaned.lower()

        if not cleaned or not schema.tables:
            raise UnsupportedDeterministicQueryError(
                "No tables available or empty question.",
                user_message=f"Cannot answer question deterministically. {_SUPPORTED_HINT}",
            )

        for bad in _UNSUPPORTED_KEYWORDS:
            if bad in lowered:
                raise UnsupportedDeterministicQueryError(
                    f"Unsupported deterministic keyword '{bad.strip()}'.",
                    user_message=(
                        "This query pattern is not supported in deterministic mode. "
                        f"{_SUPPORTED_HINT}"
                    ),
                )

        matched_tables = self._match_tables(lowered, schema)
        if not matched_tables:
            raise UnsupportedDeterministicQueryError(
                "Could not identify a target table from the question.",
                user_message=(
                    f"Could not match any database table in your question. {_SUPPORTED_HINT}"
                ),
            )

        if len(matched_tables) > 2:
            raise UnsupportedDeterministicQueryError(
                "More than two tables matched; deterministic fallback supports "
                "up to two joined tables.",
                user_message=(
                    f"Too many tables referenced for deterministic mode. {_SUPPORTED_HINT}"
                ),
            )

        primary_table = matched_tables[0]
        secondary_table = matched_tables[1] if len(matched_tables) == 2 else None
        join_spec: tuple[TableInfo, TableInfo, ForeignKeyInfo] | None = None

        if secondary_table is not None:
            join_spec = self._find_fk_join(primary_table, secondary_table)
            if join_spec is None:
                raise UnsupportedDeterministicQueryError(
                    f"No direct foreign-key relationship found between "
                    f"{primary_table.name} and {secondary_table.name}.",
                    user_message=(
                        "No direct foreign-key relationship exists between "
                        f"'{primary_table.name}' and '{secondary_table.name}'."
                    ),
                )

        active_tables = (
            [primary_table, secondary_table] if secondary_table is not None else [primary_table]
        )

        # Detect aggregate, group by, where, order by, and limit components.
        agg_func = self._detect_aggregate(lowered)
        group_col = (
            self._detect_group_by(lowered, active_tables)
            if (agg_func is not None or "group by" in lowered or "grouped by" in lowered)
            else None
        )
        where_conds, params = self._extract_where_conditions(cleaned, lowered, active_tables)
        limit_val = self._extract_limit(lowered)
        order_spec = self._detect_order_by(lowered, active_tables, agg_func, group_col)

        # Build SQLGlot AST cleanly
        query = self._build_ast(
            primary_table=primary_table,
            secondary_table=secondary_table,
            join_spec=join_spec,
            lowered_question=lowered,
            agg_func=agg_func,
            group_col=group_col,
            where_conds=where_conds,
            order_spec=order_spec,
            limit_val=limit_val,
        )

        sql = query.sql(dialect="sqlite")
        explanation = self._explainer.explain_from_ast(sql, schema)
        return GeneratedSQL(
            sql=sql,
            params=params,
            provider_name=self.name,
            mode="fallback",
            raw_explanation=explanation,
        )

    def explain_sql(self, sql: str, schema: SchemaSnapshot) -> str:
        """Explain SQL deterministically using SQLGlot AST."""
        return self._explainer.explain_from_ast(sql, schema)

    @staticmethod
    def _singularize(word: str) -> str:
        if word.endswith("ies") and len(word) > 4:
            return word[:-3] + "y"
        if word.endswith("s") and len(word) > 3:
            return word[:-1]
        return word

    def _match_tables(self, lowered: str, schema: SchemaSnapshot) -> list[TableInfo]:
        tokens = set(re.findall(r"[a-z0-9_]+", lowered))
        singular_tokens = {self._singularize(t) for t in tokens}
        all_tokens = tokens | singular_tokens

        scored: list[tuple[int, TableInfo]] = []
        for idx, table in enumerate(schema.tables):
            t_low = table.name.lower()
            t_spaced = t_low.replace("_", " ")
            t_sing = self._singularize(t_low)
            pos = -1
            if re.search(rf"\b{re.escape(t_spaced)}\b", lowered):
                pos = lowered.find(t_spaced)
            elif t_low in all_tokens or t_sing in all_tokens:
                match = re.search(
                    rf"\b(?:{re.escape(t_low)}|{re.escape(t_sing)}s?)\b",
                    lowered,
                )
                pos = match.start() if match else idx
            if pos >= 0:
                scored.append((pos, table))

        scored.sort(key=lambda item: item[0])
        return [t for _, t in scored]

    @staticmethod
    def _find_fk_join(
        table_a: TableInfo,
        table_b: TableInfo,
    ) -> tuple[TableInfo, TableInfo, ForeignKeyInfo] | None:
        for fk in table_a.foreign_keys:
            if fk.referred_table.lower() == table_b.name.lower():
                return (table_a, table_b, fk)
        for fk in table_b.foreign_keys:
            if fk.referred_table.lower() == table_a.name.lower():
                return (table_b, table_a, fk)
        return None

    @staticmethod
    def _detect_aggregate(lowered: str) -> str | None:
        if any(kw in lowered for kw in ("how many", "count ", "number of")):
            return "COUNT"
        if any(kw in lowered for kw in ("average ", "avg ", "mean ")):
            return "AVG"
        if any(kw in lowered for kw in ("sum ", "total ")):
            return "SUM"
        return None

    def _match_column(
        self,
        phrase: str,
        tables: list[TableInfo],
        *,
        numeric_only: bool = False,
    ) -> tuple[TableInfo, str] | None:
        p_low = phrase.lower()
        for table in tables:
            for col in table.columns:
                if numeric_only and not self._is_numeric_type(col.data_type):
                    continue
                c_low = col.name.lower()
                c_spaced = c_low.replace("_", " ")
                if re.search(rf"\b{re.escape(c_low)}\b", p_low) or re.search(
                    rf"\b{re.escape(c_spaced)}\b", p_low
                ):
                    return (table, col.name)
        return None

    @staticmethod
    def _is_numeric_type(data_type: str) -> bool:
        upper = data_type.upper()
        return any(tok in upper for tok in ("INT", "REAL", "FLOAT", "NUM", "DEC", "DOUBLE"))

    def _pick_numeric_column(
        self,
        lowered: str,
        tables: list[TableInfo],
    ) -> tuple[TableInfo, str] | None:
        explicit = self._match_column(lowered, tables, numeric_only=True)
        if explicit is not None:
            return explicit
        # Semantic hints (sales/revenue/amount -> total_amount/line_total; price -> unit_price)
        hints = {
            "sales": ("total_amount", "line_total"),
            "revenue": ("total_amount", "line_total"),
            "spend": ("total_amount", "line_total"),
            "amount": ("total_amount", "line_total"),
            "price": ("unit_price",),
            "cost": ("unit_price", "line_total"),
            "quantity": ("quantity",),
        }
        for keyword, preferred_cols in hints.items():
            if keyword in lowered:
                for table in tables:
                    for col_name in preferred_cols:
                        if any(c.name.lower() == col_name for c in table.columns):
                            return (table, col_name)
        # Otherwise pick first non-ID numeric column if unambiguous.
        candidates = [
            (table, col.name)
            for table in tables
            for col in table.columns
            if self._is_numeric_type(col.data_type)
            and col.name.lower() != "id"
            and not col.name.lower().endswith("_id")
        ]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _detect_group_by(
        self,
        lowered: str,
        tables: list[TableInfo],
    ) -> tuple[TableInfo, str] | None:
        match = re.search(r"\b(?:by|per)\s+([a-z0-9_ ]+)", lowered)
        if not match:
            return None
        tail = match.group(1)
        # Check for "table column" or direct column match in tail.
        col_match = self._match_column(tail, tables)
        if col_match is not None:
            return col_match
        return None

    def _extract_where_conditions(
        self,
        original: str,
        lowered: str,
        tables: list[TableInfo],
    ) -> tuple[list[exp.Expression], dict[str, Any]]:
        conditions: list[exp.Expression] = []
        params: dict[str, Any] = {}
        param_idx = 0

        def _add_param(val: Any) -> exp.Placeholder:
            nonlocal param_idx
            pname = f"p_{param_idx}"
            param_idx += 1
            params[pname] = val
            return exp.Placeholder(this=pname)

        # Pattern 1: explicit column comparison: "<col> (=|>=|<=|>|<|is|equals) <val>"
        comp_pattern = re.compile(
            r"\b([a-z][a-z0-9_ ]*?)\s+"
            r"(=|>=|<=|>|<|is|equals|above|over|greater than|more than|under|below|less than)\s+"
            r"(['\"][^'\"]+['\"]|-?\d+(?:\.\d+)?|[A-Za-z0-9_@.-]+)",
            re.IGNORECASE,
        )
        matched_cols: set[str] = set()
        for m in comp_pattern.finditer(original):
            raw_col_phrase = m.group(1).strip().lower()
            op_raw = m.group(2).strip().lower()
            raw_val = m.group(3).strip()

            col_match = self._match_column(raw_col_phrase, tables)
            if col_match is None:
                continue
            table, col_name = col_match
            # Strip quotes if present and parse numeric if numeric column.
            val_clean: Any = raw_val.strip("'\"")
            col_meta = next(c for c in table.columns if c.name == col_name)
            if self._is_numeric_type(col_meta.data_type):
                try:
                    is_int = bool(re.fullmatch(r"-?\d+", str(val_clean)))
                    val_clean = int(val_clean) if is_int else float(val_clean)
                except ValueError:
                    continue

            col_expr = exp.column(col_name, table=table.name)
            ph = _add_param(val_clean)
            if op_raw in {">", "above", "over", "greater than", "more than"}:
                conditions.append(exp.GT(this=col_expr, expression=ph))
            elif op_raw in {"<", "under", "below", "less than"}:
                conditions.append(exp.LT(this=col_expr, expression=ph))
            elif op_raw == ">=":
                conditions.append(exp.GTE(this=col_expr, expression=ph))
            elif op_raw == "<=":
                conditions.append(exp.LTE(this=col_expr, expression=ph))
            else:
                conditions.append(exp.EQ(this=col_expr, expression=ph))
            matched_cols.add(f"{table.name}.{col_name}")

        # Pattern 2: common phrasing like "from <Country>" or "status <completed>"
        from_match = re.search(r"\bfrom\s+([A-Z][A-Za-z]+)\b", original)
        if from_match:
            val = from_match.group(1)
            for table in tables:
                if any(c.name.lower() == "country" for c in table.columns) and (
                    f"{table.name}.country" not in matched_cols
                ):
                    conditions.append(
                        exp.EQ(
                            this=exp.column("country", table=table.name),
                            expression=_add_param(val),
                        )
                    )
                    matched_cols.add(f"{table.name}.country")
                    break

        # Pattern 3: status adjectives ("completed orders", "pending orders", "cancelled orders")
        for status_word in ("completed", "pending", "cancelled"):
            if re.search(rf"\b{status_word}\b", lowered):
                for table in tables:
                    if any(c.name.lower() == "status" for c in table.columns) and (
                        f"{table.name}.status" not in matched_cols
                    ):
                        conditions.append(
                            exp.EQ(
                                this=exp.column("status", table=table.name),
                                expression=_add_param(status_word),
                            )
                        )
                        matched_cols.add(f"{table.name}.status")
                        break

        return conditions, params

    def _extract_limit(self, lowered: str) -> int:
        m = re.search(r"\b(?:top|first|limit|last)\s+(\d+)\b", lowered)
        if m:
            val = max(1, min(int(m.group(1)), self._default_limit))
            return val
        return self._default_limit

    def _detect_order_by(
        self,
        lowered: str,
        tables: list[TableInfo],
        agg_func: str | None,
        group_col: tuple[TableInfo, str] | None,
    ) -> tuple[exp.Expression, bool] | None:
        desc = any(
            kw in lowered
            for kw in ("top ", "highest", "most ", "largest", "newest", "latest", "desc")
        )
        asc = any(kw in lowered for kw in ("lowest", "cheapest", "smallest", "oldest", "asc"))
        is_desc = desc and not asc

        if group_col is not None and agg_func is not None and (desc or asc):
            return (exp.column("metric_value"), is_desc)

        by_match = re.search(r"\b(?:order|sort|sorted)\s+by\s+([a-z0-9_ ]+)", lowered)
        if by_match:
            col_match = self._match_column(by_match.group(1), tables)
            if col_match is not None:
                return (exp.column(col_match[1], table=col_match[0].name), is_desc)

        if desc or asc:
            by_tail = re.search(r"\bby\s+([a-z0-9_ ]+)", lowered)
            if by_tail:
                col_match = self._match_column(by_tail.group(1), tables)
                if col_match is not None:
                    return (exp.column(col_match[1], table=col_match[0].name), is_desc)
            num_col = self._pick_numeric_column(lowered, tables)
            if num_col is not None:
                return (exp.column(num_col[1], table=num_col[0].name), is_desc)
            for table in tables:
                for col in table.columns:
                    if "date" in col.name.lower() or "created_at" in col.name.lower():
                        return (exp.column(col.name, table=table.name), is_desc)

        return None

    def _select_explicit_columns(
        self,
        lowered: str,
        tables: list[TableInfo],
    ) -> list[exp.Expression]:
        # Only treat columns mentioned in the projection portion (before WHERE/WITH/BY/SORT/ORDER)
        # as explicit column projections; otherwise return full table columns (*).
        proj_part = re.split(
            r"\b(?:where|with|by|sorted\s+by|order\s+by|ordered\s+by|from)\b",
            lowered,
            maxsplit=1,
        )[0]
        cols: list[exp.Expression] = []
        seen: set[tuple[str, str]] = set()
        for table in tables:
            for col in table.columns:
                c_low = col.name.lower()
                c_spaced = c_low.replace("_", " ")
                if re.search(rf"\b{re.escape(c_low)}\b", proj_part) or re.search(
                    rf"\b{re.escape(c_spaced)}\b", proj_part
                ):
                    key = (table.name, col.name)
                    if key not in seen:
                        seen.add(key)
                        cols.append(exp.column(col.name, table=table.name))
        return cols

    def _build_ast(
        self,
        *,
        primary_table: TableInfo,
        secondary_table: TableInfo | None,
        join_spec: tuple[TableInfo, TableInfo, ForeignKeyInfo] | None,
        lowered_question: str,
        agg_func: str | None,
        group_col: tuple[TableInfo, str] | None,
        where_conds: list[exp.Expression],
        order_spec: tuple[exp.Expression, bool] | None,
        limit_val: int,
    ) -> exp.Select:
        active_tables = (
            [primary_table, secondary_table] if secondary_table is not None else [primary_table]
        )

        select_exprs: list[exp.Expression] = []
        group_exprs: list[exp.Expression] = []

        if agg_func is not None:
            if agg_func == "COUNT":
                agg_expr: exp.Expression = exp.Count(this=exp.Star())
            else:
                num_col = self._pick_numeric_column(lowered_question, active_tables)
                if num_col is None:
                    raise UnsupportedDeterministicQueryError(
                        f"Could not identify a numeric column for {agg_func}.",
                        user_message=(
                            f"Could not determine which numeric column to {agg_func.lower()}."
                        ),
                    )
                target_col = exp.column(num_col[1], table=num_col[0].name)
                agg_expr = (
                    exp.Sum(this=target_col) if agg_func == "SUM" else exp.Avg(this=target_col)
                )

            if group_col is not None:
                g_expr = exp.column(group_col[1], table=group_col[0].name)
                select_exprs.append(g_expr)
                group_exprs.append(g_expr)

            select_exprs.append(exp.alias_(agg_expr, "metric_value"))
        else:
            if group_col is not None:
                raise UnsupportedDeterministicQueryError(
                    "GROUP BY requires an aggregation such as COUNT, SUM, or AVG.",
                    user_message=(
                        f"Grouping requires an aggregate (COUNT, SUM, or AVG). {_SUPPORTED_HINT}"
                    ),
                )
            matched_cols = self._select_explicit_columns(lowered_question, active_tables)
            if matched_cols:
                select_exprs.extend(matched_cols)
            else:
                for tbl in active_tables:
                    select_exprs.append(
                        exp.Column(this=exp.Star(), table=exp.to_identifier(tbl.name))
                    )

        query = exp.select(*select_exprs).from_(exp.to_table(primary_table.name))

        if secondary_table is not None and join_spec is not None:
            fk_owner, fk_target, fk = join_spec
            on_cond = exp.EQ(
                this=exp.column(fk.column, table=fk_owner.name),
                expression=exp.column(fk.referred_column, table=fk_target.name),
            )
            query = query.join(exp.to_table(secondary_table.name), on=on_cond)

        if where_conds:
            combined_where = where_conds[0]
            for cond in where_conds[1:]:
                combined_where = exp.And(this=combined_where, expression=cond)
            query = query.where(combined_where)

        if group_exprs:
            query = query.group_by(*group_exprs)

        if order_spec is not None:
            order_expr, is_desc = order_spec
            query = query.order_by(exp.Ordered(this=order_expr, desc=is_desc))

        query = query.limit(limit_val)
        return query
