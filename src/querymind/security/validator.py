"""SQLGlot AST safety validator, context-aware column checker, and literal binder."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import sqlglot
from sqlglot import exp

from querymind.db.engine import ALLOWED_EXECUTION_FUNCTIONS
from querymind.domain.exceptions import (
    AmbiguousColumnError,
    UnknownColumnError,
    UnknownTableError,
    UnsafeSQLError,
    UnsupportedSQLError,
)
from querymind.domain.models import ScalarParam, SchemaSnapshot, ValidatedQuery

_FORBIDDEN_EXPRESSIONS: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Pragma,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Merge,
    exp.TruncateTable,
)

# SQLGlot expressions that represent safe SQLite cast/function constructs.
_ALLOWED_FUNCTIONS: frozenset[str] = ALLOWED_EXECUTION_FUNCTIONS | {"cast", "if", "char_length"}

_FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        "load_extension",
        "fts3_tokenizer",
        "randomblob",
        "zeroblob",
        "random",
        "readfile",
        "writefile",
        "edit",
        "sqlite_version",
        "sqlite_source_id",
        "sqlite_compileoption_get",
        "sqlite_compileoption_used",
        "sqlite_offset",
        "last_insert_rowid",
        "changes",
        "total_changes",
    }
)


class _ScopeResult:
    """Resolved projected columns for a SELECT or set-operation block."""

    def __init__(self, projected_columns: set[str], has_wildcard: bool = False) -> None:
        self.projected_columns = projected_columns
        self.has_wildcard = has_wildcard


class SQLValidator:
    """Validates SQL AST safety, schema/column scope, LIMITs, and parameter bindings."""

    def __init__(self, *, default_max_rows: int = 500, max_nesting_depth: int = 3) -> None:
        self._default_max_rows = default_max_rows
        self._max_nesting_depth = max_nesting_depth

    def validate(
        self,
        sql: str,
        schema: SchemaSnapshot,
        *,
        existing_params: Mapping[str, ScalarParam] | None = None,
        max_rows: int | None = None,
    ) -> ValidatedQuery:
        """Validate ``sql`` against ``schema`` and return an immutable ``ValidatedQuery``."""
        cleaned = (sql or "").strip()
        if not cleaned:
            raise UnsafeSQLError("SQL query cannot be empty.")

        row_cap = min(max_rows, self._default_max_rows) if max_rows else self._default_max_rows

        try:
            statements = sqlglot.parse(cleaned, read="sqlite")
        except sqlglot.errors.ParseError as exc:
            raise UnsupportedSQLError(
                f"Failed to parse SQL safely: {exc}",
                user_message="Generated SQL could not be parsed safely.",
            ) from exc

        if len(statements) != 1 or statements[0] is None:
            raise UnsafeSQLError(
                "Only a single SQL SELECT statement is permitted; multiple statements rejected.",
                user_message="Multiple SQL statements are not allowed.",
            )

        root = statements[0]
        self._check_forbidden_nodes_and_functions(root)

        if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise UnsafeSQLError(
                f"Disallowed SQL statement type: {type(root).__name__}. Only SELECT is allowed.",
                user_message="Only read-only SELECT queries are permitted.",
            )

        referenced_tables: set[str] = set()
        self._validate_query_node(
            root,
            schema=schema,
            cte_scopes={},
            outer_sources={},
            depth=0,
            referenced_tables=referenced_tables,
        )

        applied_limit = self._enforce_limit(root, row_cap)
        bound_params = self._parameterize_literals(
            root,
            existing_params=dict(existing_params or {}),
        )

        final_sql = root.sql(dialect="sqlite")
        try:
            return ValidatedQuery(
                sql=final_sql,
                params=bound_params,
                referenced_tables=tuple(sorted(referenced_tables)),
                applied_limit=applied_limit,
            )
        except ValueError as exc:
            raise UnsafeSQLError(
                f"Parameter binding validation failed: {exc}",
                user_message="Query parameters did not match validated placeholders.",
            ) from exc

    def _check_forbidden_nodes_and_functions(self, root: exp.Expression) -> None:
        for node in root.walk():
            if isinstance(node, _FORBIDDEN_EXPRESSIONS):
                raise UnsafeSQLError(
                    f"Disallowed SQL operation: {type(node).__name__}.",
                    user_message="Only read-only SELECT queries are permitted.",
                )

            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise UnsafeSQLError(
                    "Recursive CTEs (WITH RECURSIVE) are not permitted.",
                    user_message="Recursive CTEs are not permitted.",
                )

            if isinstance(node, exp.Table):
                tbl = (node.name or "").lower()
                if tbl.startswith("sqlite_") or tbl.startswith("pragma_"):
                    raise UnsafeSQLError(
                        f"Access to SQLite system table '{node.name}' is forbidden.",
                        user_message="Access to internal SQLite system tables is not permitted.",
                    )

            if isinstance(node, exp.Placeholder):
                raw_name = str(node.name or node.this or "")
                if not raw_name or raw_name.isdigit():
                    raise UnsafeSQLError(
                        "Unnamed positional placeholders are not permitted.",
                        user_message="Unnamed SQL placeholders are not permitted.",
                    )

            if isinstance(node, (exp.HexString, exp.BitString)):
                raise UnsafeSQLError(
                    "Hex/bit literals are not permitted.",
                    user_message="Unsupported literal format in SQL.",
                )

            if isinstance(node, exp.Func) and not isinstance(node, (exp.Binary, exp.Unary)):
                func_name = (
                    node.name if isinstance(node, exp.Anonymous) else node.sql_name()
                ).lower()
                if func_name in _FORBIDDEN_FUNCTIONS or func_name not in _ALLOWED_FUNCTIONS:
                    raise UnsafeSQLError(
                        f"SQL function '{func_name}' is not permitted.",
                        user_message=f"SQL function '{func_name}' is not permitted.",
                    )

    def _validate_query_node(
        self,
        node: exp.Expression,
        *,
        schema: SchemaSnapshot,
        cte_scopes: dict[str, _ScopeResult],
        outer_sources: dict[str, set[str]],
        depth: int,
        referenced_tables: set[str],
    ) -> _ScopeResult:
        if depth > self._max_nesting_depth:
            raise UnsupportedSQLError(
                f"Query nesting depth exceeds maximum of {self._max_nesting_depth}.",
                user_message="Query is too deeply nested.",
            )

        local_ctes = dict(cte_scopes)
        with_clause = node.args.get("with_") or node.args.get("with")
        if isinstance(with_clause, exp.With):
            for cte in with_clause.expressions:
                if not isinstance(cte, exp.CTE) or cte.this is None:
                    raise UnsupportedSQLError("Invalid CTE definition.")
                cte_name = cte.alias_or_name.lower()
                if not cte_name:
                    raise UnsupportedSQLError("CTE must have a valid alias.")
                cte_scope = self._validate_query_node(
                    cte.this,
                    schema=schema,
                    cte_scopes=local_ctes,
                    outer_sources=outer_sources,
                    depth=depth + 1,
                    referenced_tables=referenced_tables,
                )
                local_ctes[cte_name] = cte_scope

        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            left_scope = self._validate_query_node(
                node.left,
                schema=schema,
                cte_scopes=local_ctes,
                outer_sources=outer_sources,
                depth=depth,
                referenced_tables=referenced_tables,
            )
            self._validate_query_node(
                node.right,
                schema=schema,
                cte_scopes=local_ctes,
                outer_sources=outer_sources,
                depth=depth,
                referenced_tables=referenced_tables,
            )
            order_clause = node.args.get("order")
            if isinstance(order_clause, exp.Order):
                # Validate ORDER BY against set-op left arm projections.
                self._validate_order_or_group_expressions(
                    order_clause.expressions,
                    source_map={},
                    outer_sources=outer_sources,
                    select_alias_counts=Counter({c: 1 for c in left_scope.projected_columns}),
                    num_projections=len(left_scope.projected_columns),
                    allow_positional=True,
                )
            return left_scope

        if not isinstance(node, exp.Select):
            raise UnsafeSQLError(f"Unsupported query expression: {type(node).__name__}.")

        # 1. Resolve FROM and JOIN sources
        source_map: dict[str, set[str]] = {}
        from_clause = node.args.get("from_") or node.args.get("from")
        if isinstance(from_clause, exp.From) and from_clause.this is not None:
            self._register_source(
                from_clause.this,
                schema=schema,
                cte_scopes=local_ctes,
                outer_sources={**outer_sources, **source_map},
                depth=depth,
                source_map=source_map,
                referenced_tables=referenced_tables,
            )

        joins = node.args.get("joins") or []
        for join in joins:
            if isinstance(join, exp.Join) and join.this is not None:
                self._register_source(
                    join.this,
                    schema=schema,
                    cte_scopes=local_ctes,
                    outer_sources={**outer_sources, **source_map},
                    depth=depth,
                    source_map=source_map,
                    referenced_tables=referenced_tables,
                )

        if not source_map and any(isinstance(n, exp.Column) for n in node.walk()):
            raise UnknownTableError(
                "Query references columns without a valid FROM table.",
                user_message="Query must reference a valid table.",
            )

        # 2. Validate nested subqueries in expressions (WHERE/SELECT/HAVING/etc.)
        combined_outer = {**outer_sources, **source_map}
        self._validate_nested_subqueries_in_select(
            node,
            schema=schema,
            cte_scopes=local_ctes,
            outer_sources=combined_outer,
            depth=depth,
            referenced_tables=referenced_tables,
        )

        # 3. Validate JOIN ON predicates (source_map + outer_sources only; no SELECT aliases)
        for join in joins:
            if isinstance(join, exp.Join):
                on_expr = join.args.get("on")
                if on_expr is not None:
                    self._validate_columns_in_expr(
                        on_expr,
                        source_map=source_map,
                        outer_sources=outer_sources,
                        select_alias_counts=None,
                    )

        # 4. Validate SELECT projection columns and compute output columns + alias counts
        projected_columns, select_alias_counts, has_wildcard = self._validate_projections(
            node.expressions,
            source_map=source_map,
            outer_sources=outer_sources,
        )

        # 5. Validate WHERE clause (no SELECT aliases allowed in WHERE)
        where_clause = node.args.get("where")
        if isinstance(where_clause, exp.Where) and where_clause.this is not None:
            self._validate_columns_in_expr(
                where_clause.this,
                source_map=source_map,
                outer_sources=outer_sources,
                select_alias_counts=None,
            )

        # 6. Validate GROUP BY, HAVING, and ORDER BY (SQLite allows SELECT aliases here)
        group_clause = node.args.get("group")
        if isinstance(group_clause, exp.Group) and group_clause.expressions:
            self._validate_order_or_group_expressions(
                group_clause.expressions,
                source_map=source_map,
                outer_sources=outer_sources,
                select_alias_counts=select_alias_counts,
                num_projections=len(node.expressions),
                allow_positional=True,
            )

        having_clause = node.args.get("having")
        if isinstance(having_clause, exp.Having) and having_clause.this is not None:
            self._validate_columns_in_expr(
                having_clause.this,
                source_map=source_map,
                outer_sources=outer_sources,
                select_alias_counts=select_alias_counts,
            )

        order_clause = node.args.get("order")
        if isinstance(order_clause, exp.Order) and order_clause.expressions:
            self._validate_order_or_group_expressions(
                order_clause.expressions,
                source_map=source_map,
                outer_sources=outer_sources,
                select_alias_counts=select_alias_counts,
                num_projections=len(node.expressions),
                allow_positional=True,
            )

        return _ScopeResult(projected_columns=projected_columns, has_wildcard=has_wildcard)

    def _register_source(
        self,
        source_expr: exp.Expression,
        *,
        schema: SchemaSnapshot,
        cte_scopes: dict[str, _ScopeResult],
        outer_sources: dict[str, set[str]],
        depth: int,
        source_map: dict[str, set[str]],
        referenced_tables: set[str],
    ) -> None:
        if isinstance(source_expr, exp.Subquery):
            if source_expr.this is None:
                raise UnsupportedSQLError("Empty subquery is not permitted.")
            sub_scope = self._validate_query_node(
                source_expr.this,
                schema=schema,
                cte_scopes=cte_scopes,
                outer_sources=outer_sources,
                depth=depth + 1,
                referenced_tables=referenced_tables,
            )
            alias = (source_expr.alias or f"__sub_{len(source_map)}").lower()
            source_map[alias] = sub_scope.projected_columns
            return

        if isinstance(source_expr, exp.Table):
            table_name = (source_expr.name or "").lower()
            alias = (source_expr.alias or table_name).lower()
            if table_name in cte_scopes:
                source_map[alias] = set(cte_scopes[table_name].projected_columns)
                return

            table_info = schema.get_table(table_name)
            if table_info is None:
                raise UnknownTableError(
                    f"Table '{source_expr.name}' does not exist in the discovered schema.",
                    user_message=f"Unknown table '{source_expr.name}'.",
                )
            referenced_tables.add(table_info.name)
            source_map[alias] = {col.lower() for col in table_info.column_names()}
            return

        raise UnsupportedSQLError(f"Unsupported FROM/JOIN source: {type(source_expr).__name__}.")

    def _validate_nested_subqueries_in_select(
        self,
        select_node: exp.Select,
        *,
        schema: SchemaSnapshot,
        cte_scopes: dict[str, _ScopeResult],
        outer_sources: dict[str, set[str]],
        depth: int,
        referenced_tables: set[str],
    ) -> None:
        # Walk subquery expressions outside FROM/JOIN (already checked in _register_source).
        for sub in select_node.find_all(exp.Subquery):
            parent = sub.parent
            if isinstance(parent, (exp.From, exp.Join)):
                continue
            # Validate only immediate child subqueries; deeper ones recurse naturally.
            if self._nearest_enclosing_select(sub) is select_node and sub.this is not None:
                self._validate_query_node(
                    sub.this,
                    schema=schema,
                    cte_scopes=cte_scopes,
                    outer_sources=outer_sources,
                    depth=depth + 1,
                    referenced_tables=referenced_tables,
                )

    @staticmethod
    def _nearest_enclosing_select(node: exp.Expression) -> exp.Select | None:
        cur = node.parent
        while cur is not None:
            if isinstance(cur, exp.Select):
                return cur
            cur = cur.parent
        return None

    def _validate_projections(
        self,
        expressions: list[exp.Expression],
        *,
        source_map: dict[str, set[str]],
        outer_sources: dict[str, set[str]],
    ) -> tuple[set[str], Counter[str], bool]:
        projected: set[str] = set()
        alias_counts: Counter[str] = Counter()
        has_wildcard = False

        for expr in expressions:
            if isinstance(expr, exp.Star):
                if not source_map:
                    raise UnknownTableError("Cannot use '*' without a FROM table.")
                has_wildcard = True
                for cols in source_map.values():
                    projected.update(cols)
                continue

            if isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star):
                qualifier = (expr.table or "").lower()
                if qualifier not in source_map:
                    raise UnknownTableError(
                        f"Unknown table or alias '{expr.table}' in '{expr.sql(dialect='sqlite')}'.",
                        user_message=f"Unknown table or alias '{expr.table}'.",
                    )
                has_wildcard = True
                projected.update(source_map[qualifier])
                continue

            self._validate_columns_in_expr(
                expr,
                source_map=source_map,
                outer_sources=outer_sources,
                select_alias_counts=None,
            )

            if isinstance(expr, exp.Alias):
                alias_name = expr.alias.lower()
                projected.add(alias_name)
                alias_counts[alias_name] += 1
            elif isinstance(expr, exp.Column):
                projected.add(expr.name.lower())

        return projected, alias_counts, has_wildcard

    def _validate_order_or_group_expressions(
        self,
        expressions: list[exp.Expression],
        *,
        source_map: dict[str, set[str]],
        outer_sources: dict[str, set[str]],
        select_alias_counts: Counter[str],
        num_projections: int,
        allow_positional: bool,
    ) -> None:
        for item in expressions:
            inner = item.this if isinstance(item, exp.Ordered) else item
            if allow_positional and isinstance(inner, exp.Literal) and inner.is_int:
                pos = int(str(inner.this))
                if pos < 1 or pos > max(num_projections, 1):
                    raise UnknownColumnError(
                        f"Positional column index {pos} is out of range.",
                        user_message=f"Column position {pos} is out of range.",
                    )
                continue

            self._validate_columns_in_expr(
                item,
                source_map=source_map,
                outer_sources=outer_sources,
                select_alias_counts=select_alias_counts,
            )

    def _validate_columns_in_expr(
        self,
        expr: exp.Expression,
        *,
        source_map: dict[str, set[str]],
        outer_sources: dict[str, set[str]],
        select_alias_counts: Counter[str] | None,
    ) -> None:
        for col in self._columns_in_current_scope(expr):
            if isinstance(col.this, exp.Star):
                qualifier = (col.table or "").lower()
                if qualifier and qualifier not in source_map:
                    raise UnknownTableError(
                        f"Unknown table or alias '{col.table}'.",
                        user_message=f"Unknown table or alias '{col.table}'.",
                    )
                continue

            col_name = (col.name or "").lower()
            qualifier = (col.table or "").lower()

            if qualifier:
                if qualifier in source_map:
                    if col_name not in source_map[qualifier]:
                        raise UnknownColumnError(
                            f"Column '{col.name}' does not exist on '{col.table}'.",
                            user_message=f"Unknown column '{col.table}.{col.name}'.",
                        )
                elif qualifier in outer_sources:
                    if col_name not in outer_sources[qualifier]:
                        raise UnknownColumnError(
                            f"Column '{col.name}' does not exist on outer source '{col.table}'.",
                            user_message=f"Unknown column '{col.table}.{col.name}'.",
                        )
                else:
                    raise UnknownTableError(
                        f"Unknown table or alias '{col.table}'.",
                        user_message=f"Unknown table or alias '{col.table}'.",
                    )
                continue

            # Unqualified column resolution: check SELECT aliases (when allowed) and source_map.
            if select_alias_counts is not None and col_name in select_alias_counts:
                if select_alias_counts[col_name] > 1:
                    raise AmbiguousColumnError(
                        f"SELECT alias '{col.name}' is ambiguous.",
                        user_message=f"Column or alias '{col.name}' is ambiguous.",
                    )
                continue

            matching_sources = [src for src, cols in source_map.items() if col_name in cols]
            if len(matching_sources) == 1:
                continue
            if len(matching_sources) > 1:
                raise AmbiguousColumnError(
                    f"Column '{col.name}' is ambiguous across joined sources: "
                    f"{', '.join(sorted(matching_sources))}.",
                    user_message=(
                        f"Column '{col.name}' is ambiguous; please qualify it with a table alias."
                    ),
                )

            # Fall back to outer correlated scope only if not found locally.
            outer_matches = [src for src, cols in outer_sources.items() if col_name in cols]
            if len(outer_matches) == 1:
                continue
            if len(outer_matches) > 1:
                raise AmbiguousColumnError(
                    f"Column '{col.name}' is ambiguous across outer sources.",
                    user_message=f"Column '{col.name}' is ambiguous.",
                )

            raise UnknownColumnError(
                f"Unknown column '{col.name}' in current query scope.",
                user_message=f"Unknown column '{col.name}'.",
            )

    @staticmethod
    def _columns_in_current_scope(expr: exp.Expression) -> list[exp.Column]:
        """Collect Column nodes in ``expr`` without crossing into nested Subquery nodes."""
        cols: list[exp.Column] = []
        stack: list[exp.Expression] = [expr]
        while stack:
            current = stack.pop()
            if isinstance(current, exp.Subquery) and current is not expr:
                continue
            if isinstance(current, exp.Column):
                cols.append(current)
            else:
                for child in current.iter_expressions():
                    stack.append(child)
        return cols

    def _enforce_limit(self, root: exp.Expression, row_cap: int) -> int:
        limit_node = root.args.get("limit")
        if limit_node is None:
            root.set("limit", exp.Limit(expression=exp.Literal.number(row_cap)))
            return row_cap

        if not isinstance(limit_node, exp.Limit) or not isinstance(
            limit_node.expression, exp.Literal
        ):
            raise UnsafeSQLError(
                "LIMIT clause must be a positive integer literal.",
                user_message="LIMIT must be a positive integer.",
            )

        if not limit_node.expression.is_int:
            raise UnsafeSQLError(
                "LIMIT clause must be an integer.",
                user_message="LIMIT must be a positive integer.",
            )

        requested = int(str(limit_node.expression.this))
        if requested < 1:
            raise UnsafeSQLError(
                "LIMIT must be at least 1.",
                user_message="LIMIT must be at least 1.",
            )

        capped = min(requested, row_cap)
        if capped != requested:
            limit_node.set("expression", exp.Literal.number(capped))
        return capped

    def _parameterize_literals(
        self,
        root: exp.Expression,
        *,
        existing_params: dict[str, ScalarParam],
    ) -> dict[str, ScalarParam]:
        """Extract scalar literals into bound params while keeping valid placeholders."""
        params: dict[str, ScalarParam] = dict(existing_params)
        counter = 0

        def _next_param_name() -> str:
            nonlocal counter
            while f"p_{counter}" in params:
                counter += 1
            name = f"p_{counter}"
            counter += 1
            return name

        # Keep structural integer literals (LIMIT, OFFSET, positional ORDER BY) unparameterized.
        structural_nodes: set[int] = set()
        for limit_node in root.find_all(exp.Limit, exp.Offset):
            if limit_node.expression is not None:
                structural_nodes.add(id(limit_node.expression))
        for order_node in root.find_all(exp.Order):
            for ordered in order_node.expressions:
                inner = ordered.this if isinstance(ordered, exp.Ordered) else ordered
                if isinstance(inner, exp.Literal) and inner.is_int:
                    structural_nodes.add(id(inner))

        # Walk literals/booleans/negative-numbers and replace with :p_N placeholders.
        for node in list(root.walk()):
            if id(node) in structural_nodes:
                continue

            if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
                if id(node.this) in structural_nodes or not node.this.is_number:
                    continue
                raw_num = str(node.this.this)
                val: Any = -int(raw_num) if node.this.is_int else -float(raw_num)
                pname = _next_param_name()
                params[pname] = val
                node.replace(exp.Placeholder(this=pname))
                continue

            if isinstance(node, exp.Literal):
                # Skip if parent is Neg (handled above) or if already replaced.
                if isinstance(node.parent, exp.Neg):
                    continue
                if node.is_string:
                    val = str(node.this)
                elif node.is_int:
                    val = int(str(node.this))
                elif node.is_number:
                    val = float(str(node.this))
                else:
                    raise UnsafeSQLError("Unsupported literal node in SQL.")
                pname = _next_param_name()
                params[pname] = val
                node.replace(exp.Placeholder(this=pname))
                continue

            if isinstance(node, exp.Boolean):
                pname = _next_param_name()
                params[pname] = bool(node.this)
                node.replace(exp.Placeholder(this=pname))

        return params
