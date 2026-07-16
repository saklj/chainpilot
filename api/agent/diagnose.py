"""Read-only ReAct diagnosis agent for material shortage root causes."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import duckdb

from analytics.risk import database_path

from .guardrail import verify_answer
from .llm import DeepSeekClient, LLMResult, TokenUsage
from .safe_sql import SafeResult, execute_safe

Category = Literal[
    "single_source_supply",
    "shared_demand_competition",
    "long_leadtime_no_po",
    "forecast_miss",
    "unknown",
]
CATEGORIES: tuple[Category, ...] = (
    "single_source_supply",
    "shared_demand_competition",
    "long_leadtime_no_po",
    "forecast_miss",
    "unknown",
)
TOOL_SPECS = {
    "get_risk_detail": "args: material_pn; current risk, supply profile and inventory",
    "get_po_status": "args: material_pn; future PO details and timing buckets",
    "get_shared_demand": "args: material_pn; SKU demand split and SKU count",
    "get_forecast_error": "args: none; model-level forecast accuracy only",
    "query_sql": "args: sql; safe read-only SELECT fallback (limit 50)",
}


class DiagnosisLLM(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        timeout: float = 30,
    ) -> LLMResult: ...


@dataclass(frozen=True)
class ToolObservation:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    text: str


@dataclass(frozen=True)
class DiagnosisTrace:
    action: str
    args: dict[str, Any]
    observation: str


@dataclass(frozen=True)
class DiagnosisResult:
    category: Category
    root_cause: str
    steps: int
    trace: list[DiagnosisTrace]
    usage: TokenUsage
    degraded: bool
    guardrail_verdict: str


def _observation(columns: list[str], rows: list[tuple[Any, ...]]) -> ToolObservation:
    rendered = [dict(zip(columns, row, strict=True)) for row in rows]
    return ToolObservation(columns, rows, json.dumps(rendered, ensure_ascii=False, default=str))


def get_risk_detail(connection: duckdb.DuckDBPyConnection, material_pn: str) -> ToolObservation:
    row = connection.execute(
        "WITH profile AS (SELECT material_pn, count(*)::INTEGER supplier_count, "
        "min(lead_time_days)::INTEGER min_lt, max(split_pct)::DOUBLE concentration, "
        "arg_max(supplier_id, split_pct) primary_supplier FROM supply_split GROUP BY 1), "
        "inventory AS (SELECT material_pn, snapshot_date, qty_onhand FROM inventory_onhand "
        "WHERE snapshot_date <= (SELECT max(date) FROM sales_daily) "
        "QUALIFY row_number() OVER (PARTITION BY material_pn ORDER BY snapshot_date DESC)=1) "
        "SELECT r.material_pn, r.risk_level, r.doi_days, r.gap_qty, r.gap_date, "
        "p.supplier_count, p.min_lt, p.concentration, p.primary_supplier, "
        "i.qty_onhand, i.snapshot_date FROM material_risk r JOIN profile p USING(material_pn) "
        "JOIN inventory i USING(material_pn) WHERE r.material_pn=? "
        "AND r.eval_date=(SELECT max(eval_date) FROM material_risk)",
        [material_pn],
    ).fetchone()
    if row is None:
        raise ValueError(f"Material {material_pn} not found")
    columns = ["material_pn", "risk_level", "doi_days", "gap_qty", "gap_date", "supplier_count", "min_lt", "concentration", "primary_supplier", "qty_onhand", "snapshot_date"]
    return _observation(columns, [row])


def get_po_status(connection: duckdb.DuckDBPyConnection, material_pn: str) -> ToolObservation:
    rows = connection.execute(
        "WITH context AS (SELECT max(date) eval_date FROM sales_daily), risk AS ("
        "SELECT gap_date FROM material_risk WHERE material_pn=? "
        "AND eval_date=(SELECT max(eval_date) FROM material_risk)) "
        "SELECT po_id, supplier_id, eta_date, qty, CASE "
        "WHEN eta_date <= coalesce(risk.gap_date, context.eval_date) THEN 'before_gap' "
        "WHEN eta_date <= context.eval_date + INTERVAL 28 DAY THEN 'within_horizon' "
        "ELSE 'outside_horizon' END bucket FROM open_po, context, risk "
        "WHERE material_pn=? AND eta_date>context.eval_date ORDER BY eta_date, po_id",
        [material_pn, material_pn],
    ).fetchall()
    columns = ["po_id", "supplier_id", "eta_date", "qty", "bucket"]
    totals: dict[str, int] = {"before_gap": 0, "within_horizon": 0, "outside_horizon": 0}
    for row in rows:
        totals[str(row[4])] += int(row[3])
    text = json.dumps(
        {"po_count": len(rows), "bucket_qty": totals, "details": [dict(zip(columns, row, strict=True)) for row in rows]},
        ensure_ascii=False,
        default=str,
    )
    return ToolObservation(columns, rows, text)


def get_shared_demand(connection: duckdb.DuckDBPyConnection, material_pn: str) -> ToolObservation:
    rows = connection.execute(
        "WITH demand AS (SELECT f.sku_id, sum(f.yhat*b.qty_per_unit)::DOUBLE demand_qty "
        "FROM forecast_daily f JOIN bom b USING(sku_id) WHERE f.model_name='lightgbm' "
        "AND b.material_pn=? GROUP BY f.sku_id), total AS (SELECT sum(demand_qty) qty FROM demand) "
        "SELECT sku_id, demand_qty, CASE WHEN qty=0 THEN 0 ELSE demand_qty/qty*100 END share_pct, "
        "count(*) OVER()::INTEGER sku_count FROM demand, total ORDER BY demand_qty DESC, sku_id",
        [material_pn],
    ).fetchall()
    return _observation(["sku_id", "demand_qty", "share_pct", "sku_count"], rows)


def get_forecast_error(connection: duckdb.DuckDBPyConnection) -> ToolObservation:
    """Return model-level accuracy only; it can only coarsely rule out forecast unreliability."""
    rows = connection.execute(
        "SELECT model_name, avg(mape)::DOUBLE mape, avg(wmape)::DOUBLE wmape, "
        "avg(wrmsse)::DOUBLE wrmsse FROM forecast_metrics GROUP BY model_name ORDER BY model_name"
    ).fetchall()
    return _observation(["model_name", "mape", "wmape", "wrmsse"], rows)


def query_sql(connection: duckdb.DuckDBPyConnection, sql: str) -> ToolObservation:
    result = execute_safe(sql, connection=connection, limit=50)
    if not result.ok:
        return _observation(["error"], [(result.rejected_reason,)])
    return _observation(result.columns, result.rows)


def _system_prompt() -> str:
    tools = "\n".join(f"- {name}: {description}" for name, description in TOOL_SPECS.items())
    categories = ", ".join(CATEGORIES)
    return (
        "你是只读供应链缺料诊断 Agent。每步只输出一个 JSON 对象，不要 markdown。\n"
        f"工具：\n{tools}\n类别：{categories}\n"
        '行动格式：{"thought":"...","action":"工具名","args":{...}}\n'
        '结论格式：{"action":"final","category":"类别","root_cause":"引用工具数字的一段归因"}'
    )


def _parse_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("response does not contain a valid JSON object")


def _add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(left.prompt_tokens + right.prompt_tokens, left.completion_tokens + right.completion_tokens)


def _dispatch(connection: duckdb.DuckDBPyConnection, action: str, args: dict[str, Any]) -> ToolObservation:
    if action == "get_risk_detail":
        return get_risk_detail(connection, str(args.get("material_pn", "")))
    if action == "get_po_status":
        return get_po_status(connection, str(args.get("material_pn", "")))
    if action == "get_shared_demand":
        return get_shared_demand(connection, str(args.get("material_pn", "")))
    if action == "get_forecast_error":
        return get_forecast_error(connection)
    if action == "query_sql":
        return query_sql(connection, str(args.get("sql", "")))
    raise ValueError(f"Unknown tool: {action}")


def _unknown(trace: list[DiagnosisTrace], usage: TokenUsage, steps: int) -> DiagnosisResult:
    excluded = "/".join(dict.fromkeys(item.action for item in trace)) or "无可用证据"
    return DiagnosisResult("unknown", f"未定位根因，已排除或检查：{excluded}。", steps, trace, usage, True, "not_run")


def diagnose_material(
    llm: DiagnosisLLM,
    connection: duckdb.DuckDBPyConnection,
    material_pn: str,
    max_steps: int = 8,
) -> DiagnosisResult:
    get_risk_detail(connection, material_pn)
    messages = [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": f"诊断 {material_pn} 为什么缺料。"}]
    trace: list[DiagnosisTrace] = []
    observations: list[ToolObservation] = []
    usage = TokenUsage()
    for step in range(1, max_steps + 1):
        if step == max_steps:
            messages.append({"role": "user", "content": "这是最后一步，必须输出 final。"})
        parsed: dict[str, Any] | None = None
        for attempt in range(2):
            response = llm.chat(messages, temperature=0.0, timeout=30)
            usage = _add_usage(usage, response.usage)
            messages.append({"role": "assistant", "content": response.content})
            try:
                parsed = _parse_json(response.content)
                break
            except ValueError as error:
                messages.append({"role": "user", "content": f"JSON 解析错误：{error}。只输出一个合法 JSON 对象。"})
        if parsed is None:
            return _unknown(trace, usage, step)
        action = str(parsed.get("action", ""))
        if action == "final":
            category = str(parsed.get("category", "unknown"))
            if category not in CATEGORIES:
                category = "unknown"
            root_cause = str(parsed.get("root_cause", "")).strip()
            evidence = SafeResult(True, ["evidence"], [row for item in observations for row in item.rows], sum(len(item.rows) for item in observations))
            verdict = verify_answer(root_cause, evidence, f"诊断 {material_pn}")
            degraded = verdict.verdict == "fail" or not root_cause
            if degraded:
                root_cause = f"{material_pn} 诊断已降级：" + (observations[-1].text if observations else "没有可核验的工具证据。")
            return DiagnosisResult(category, root_cause, step, trace, usage, degraded, verdict.verdict)
        args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        try:
            observation = _dispatch(connection, action, args)
        except ValueError as error:
            observation = _observation(["error"], [(str(error),)])
        trace.append(DiagnosisTrace(action, args, observation.text))
        observations.append(observation)
        messages.append({"role": "user", "content": f"observation: {observation.text}"})
    return _unknown(trace, usage, max_steps)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("material_pn")
    args = parser.parse_args()
    connection = duckdb.connect(str(database_path()), read_only=True)
    try:
        result = diagnose_material(DeepSeekClient(), connection, args.material_pn)
    finally:
        connection.close()
    for index, item in enumerate(result.trace, 1):
        print(f"{index}. {item.action} {item.args} -> {item.observation}")
    print(f"category={result.category}\nroot_cause={result.root_cause}\nguardrail={result.guardrail_verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
