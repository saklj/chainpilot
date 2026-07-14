"""Deterministic read-only SQL validation and execution for DuckDB."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import AbstractSet, Any, Final

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]

ALLOWED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "sales_daily",
        "calendar",
        "prices",
        "products",
        "materials",
        "bom",
        "suppliers",
        "supply_split",
        "inventory_onhand",
        "open_po",
        "forecast_daily",
        "forecast_metrics",
        "material_risk",
        "v_material_demand_daily",
        "v_risk_by_commodity",
        "v_risk_by_item_group",
        "v_risk_by_supplier",
    }
)


@dataclass(frozen=True)
class SafeResult:
    """Structured outcome for either safe rows or a deterministic rejection."""

    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    row_count: int = 0
    rejected_reason: str | None = None
    final_sql: str | None = None


def database_path() -> Path:
    """Resolve DUCKDB_PATH relative to the repository root, matching analytics.risk."""
    configured = Path(os.environ.get("DUCKDB_PATH", "data/chainpilot.duckdb"))
    return configured if configured.is_absolute() else REPO_ROOT / configured


def _reject(reason: str, final_sql: str | None = None) -> SafeResult:
    return SafeResult(ok=False, rejected_reason=reason, final_sql=final_sql)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _cte_names(ast: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for node in _walk(ast):
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            for item in cte_map.get("map", []):
                if isinstance(item, dict) and isinstance(item.get("key"), str):
                    names.add(item["key"].lower())
    return names


def _referenced_tables(ast: dict[str, Any]) -> tuple[set[str], bool]:
    ctes = _cte_names(ast)
    tables: set[str] = set()
    unsupported_from = False
    for node in _walk(ast):
        node_type = node.get("type")
        if not isinstance(node_type, str):
            continue
        if node_type == "BASE_TABLE" and isinstance(node.get("table_name"), str):
            table = node["table_name"].lower()
            if table not in ctes:
                tables.add(table)
        elif node_type in {"TABLE_FUNCTION", "EXPRESSION_LIST"}:
            unsupported_from = True
    return tables, unsupported_from


def _has_top_level_limit(ast: dict[str, Any]) -> bool:
    statements = ast.get("statements", [])
    if len(statements) != 1:
        return False
    node = statements[0].get("node", {})
    return any(item.get("type") == "LIMIT_MODIFIER" for item in node.get("modifiers", []))


def execute_safe(
    sql: str,
    *,
    limit: int = 200,
    timeout_s: float = 5,
    allowed_tables: AbstractSet[str] | None = None,
    db_path: str | Path | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> SafeResult:
    """Validate one SELECT against its AST, enforce LIMIT, and execute read-only."""
    if not isinstance(sql, str) or not sql.strip():
        return _reject("parse_error: SQL is empty")
    if limit <= 0:
        return _reject("invalid_limit: limit must be positive")
    if timeout_s <= 0:
        return _reject("invalid_timeout: timeout_s must be positive")

    parser = duckdb.connect(":memory:")
    try:
        try:
            statements = parser.extract_statements(sql)
        except duckdb.Error as exc:
            return _reject(f"parse_error: {exc}")
        if len(statements) != 1:
            return _reject("multiple_statements: exactly one SQL statement is allowed")
        if statements[0].type != duckdb.StatementType.SELECT:
            return _reject("not_select: only SELECT statements are allowed")
        serialized = parser.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0]
        ast = json.loads(serialized)
        if ast.get("error"):
            return _reject("not_select: SQL is not a serializable SELECT statement")
    except (duckdb.Error, json.JSONDecodeError, TypeError) as exc:
        return _reject(f"parse_error: AST serialization failed: {exc}")
    finally:
        parser.close()

    if len(ast.get("statements", [])) != 1:
        return _reject("multiple_statements: exactly one SQL statement is allowed")
    tables, unsupported_from = _referenced_tables(ast)
    if unsupported_from:
        return _reject("table_not_allowed: table functions and VALUES sources are not allowed")
    source_whitelist = ALLOWED_TABLES if allowed_tables is None else allowed_tables
    whitelist = {name.lower() for name in source_whitelist}
    disallowed = sorted(tables - whitelist)
    if disallowed:
        return _reject(f"table_not_allowed: {', '.join(disallowed)}")

    clean_sql = sql.strip().rstrip(";").strip()
    final_sql = (
        clean_sql
        if _has_top_level_limit(ast)
        else f"SELECT * FROM ({clean_sql}) AS safe_query LIMIT {int(limit)}"
    )
    owns_connection = connection is None
    if connection is None:
        path = Path(db_path) if db_path is not None else database_path()
        try:
            connection = duckdb.connect(str(path), read_only=True)
        except duckdb.Error as exc:
            return _reject(f"execution_error: cannot open database read-only: {exc}", final_sql)

    timed_out = threading.Event()

    def interrupt() -> None:
        timed_out.set()
        assert connection is not None
        connection.interrupt()

    timer = threading.Timer(timeout_s, interrupt)
    timer.daemon = True
    timer.start()
    try:
        cursor = connection.execute(final_sql)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
        return SafeResult(
            ok=True,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            final_sql=final_sql,
        )
    except Exception as exc:  # Execution and result-conversion failures stay structured.
        reason = "timeout: query exceeded execution deadline" if timed_out.is_set() else f"execution_error: {exc}"
        return _reject(reason, final_sql)
    finally:
        timer.cancel()
        if owns_connection:
            connection.close()
