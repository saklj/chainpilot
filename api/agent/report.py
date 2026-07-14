"""Generate a deterministic weekly risk report with guarded LLM narratives."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Protocol

import duckdb

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.risk import database_path, explain_risk

if __package__:
    from .guardrail import verify_answer
    from .llm import DeepSeekClient, LLMResult, TokenUsage
    from .safe_sql import SafeResult
else:
    from agent.guardrail import verify_answer
    from agent.llm import DeepSeekClient, LLMResult, TokenUsage
    from agent.safe_sql import SafeResult

OVERVIEW_SECTION = "overview"
RECOMMENDATIONS_SECTION = "recommendations"
ALL_NARRATIVE_SECTIONS = [OVERVIEW_SECTION, RECOMMENDATIONS_SECTION]
ZERO_USAGE = TokenUsage()


class ReportLLM(Protocol):
    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.0,
        timeout: float = 30,
    ) -> LLMResult: ...


@dataclass(frozen=True)
class RiskKPI:
    red_count: int
    orange_count: int
    yellow_count: int
    green_count: int
    total_gap_qty: int
    red_orange_pct: Decimal


@dataclass(frozen=True)
class RiskComparison:
    previous_date: date | None
    red_change: int | None
    orange_change: int | None


@dataclass(frozen=True)
class TopRiskMaterial:
    material_pn: str
    material_name: str
    doi_days: Decimal
    gap_qty: int
    gap_date: date | None
    risk_reasons: str
    supplier_concentration: Decimal
    min_lt: int
    primary_supplier_id: str


@dataclass(frozen=True)
class SupplierExposure:
    supplier_id: str
    supplier_name: str
    red_orange_material_count: int
    weighted_gap_qty: Decimal


@dataclass(frozen=True)
class CommodityRisk:
    commodity: str
    red_count: int
    orange_count: int
    yellow_count: int
    green_count: int
    total_gap_qty: int


@dataclass(frozen=True)
class ReportData:
    report_date: date
    kpi: RiskKPI
    comparison: RiskComparison
    top_risks: list[TopRiskMaterial]
    supplier_exposure: list[SupplierExposure]
    commodity_distribution: list[CommodityRisk]


@dataclass(frozen=True)
class ReportResult:
    report_date: date
    content_md: str
    narrative_fallbacks: list[str]
    usage: TokenUsage


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _optional_date(value: Any) -> date | None:
    return None if value is None else _as_date(value)


def assemble_report_data(connection: duckdb.DuckDBPyConnection) -> ReportData:
    """Load the latest risk snapshot and every deterministic weekly-report section."""
    latest = connection.execute("SELECT max(eval_date) FROM material_risk").fetchone()[0]
    if latest is None:
        raise ValueError("material_risk is empty; cannot generate a weekly report")
    report_date = _as_date(latest)

    counts = connection.execute(
        "SELECT "
        "count(*) FILTER (WHERE risk_level = 'RED'), "
        "count(*) FILTER (WHERE risk_level = 'ORANGE'), "
        "count(*) FILTER (WHERE risk_level = 'YELLOW'), "
        "count(*) FILTER (WHERE risk_level = 'GREEN'), "
        "coalesce(sum(gap_qty), 0), count(*) "
        "FROM material_risk WHERE eval_date = ?",
        [report_date],
    ).fetchone()
    red_count, orange_count, yellow_count, green_count, total_gap_qty, total = (
        int(value) for value in counts
    )
    red_orange_pct = (
        Decimal(red_count + orange_count) * Decimal(100) / Decimal(total)
        if total
        else Decimal(0)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    kpi = RiskKPI(
        red_count=red_count,
        orange_count=orange_count,
        yellow_count=yellow_count,
        green_count=green_count,
        total_gap_qty=total_gap_qty,
        red_orange_pct=red_orange_pct,
    )

    previous = connection.execute(
        "SELECT max(eval_date) FROM material_risk WHERE eval_date < ?", [report_date]
    ).fetchone()[0]
    if previous is None:
        comparison = RiskComparison(None, None, None)
    else:
        previous_date = _as_date(previous)
        previous_counts = connection.execute(
            "SELECT "
            "count(*) FILTER (WHERE risk_level = 'RED'), "
            "count(*) FILTER (WHERE risk_level = 'ORANGE') "
            "FROM material_risk WHERE eval_date = ?",
            [previous_date],
        ).fetchone()
        comparison = RiskComparison(
            previous_date=previous_date,
            red_change=red_count - int(previous_counts[0]),
            orange_change=orange_count - int(previous_counts[1]),
        )

    top_rows = connection.execute(
        "WITH supply_profile AS ("
        "  SELECT material_pn, min(lead_time_days) AS min_lt, "
        "         arg_max(supplier_id, split_pct) AS primary_supplier_id "
        "  FROM supply_split GROUP BY material_pn"
        ") "
        "SELECT r.material_pn, m.material_name, r.doi_days, r.gap_qty, r.gap_date, "
        "       r.risk_reasons, r.supplier_concentration, "
        "       coalesce(sp.min_lt, 0), coalesce(sp.primary_supplier_id, '未知供应商') "
        "FROM material_risk r JOIN materials m USING (material_pn) "
        "LEFT JOIN supply_profile sp USING (material_pn) "
        "WHERE r.eval_date = ? AND r.risk_level = 'RED' "
        "ORDER BY r.gap_qty DESC, r.material_pn LIMIT 10",
        [report_date],
    ).fetchall()
    top_risks = [
        TopRiskMaterial(
            material_pn=str(row[0]),
            material_name=str(row[1]),
            doi_days=_decimal(row[2]),
            gap_qty=int(row[3]),
            gap_date=_optional_date(row[4]),
            risk_reasons=str(row[5] or ""),
            supplier_concentration=_decimal(row[6]),
            min_lt=int(row[7]),
            primary_supplier_id=str(row[8]),
        )
        for row in top_rows
    ]

    supplier_rows = connection.execute(
        "SELECT supplier_id, supplier_name, red_orange_material_count, weighted_gap_qty "
        "FROM v_risk_by_supplier WHERE eval_date = ? "
        "ORDER BY weighted_gap_qty DESC, supplier_id LIMIT 5",
        [report_date],
    ).fetchall()
    supplier_exposure = [
        SupplierExposure(
            supplier_id=str(row[0]),
            supplier_name=str(row[1]),
            red_orange_material_count=int(row[2]),
            weighted_gap_qty=_decimal(row[3]),
        )
        for row in supplier_rows
    ]

    commodity_rows = connection.execute(
        "SELECT commodity, red_count, orange_count, yellow_count, green_count, "
        "       total_gap_qty "
        "FROM v_risk_by_commodity WHERE eval_date = ? "
        "ORDER BY red_count DESC, orange_count DESC, commodity",
        [report_date],
    ).fetchall()
    commodity_distribution = [
        CommodityRisk(
            commodity=str(row[0]),
            red_count=int(row[1]),
            orange_count=int(row[2]),
            yellow_count=int(row[3]),
            green_count=int(row[4]),
            total_gap_qty=int(row[5]),
        )
        for row in commodity_rows
    ]
    return ReportData(
        report_date=report_date,
        kpi=kpi,
        comparison=comparison,
        top_risks=top_risks,
        supplier_exposure=supplier_exposure,
        commodity_distribution=commodity_distribution,
    )


def build_report_evidence(data: ReportData) -> SafeResult:
    """Flatten every report input into rows understood by ``verify_answer``."""
    rows: list[tuple[Any, ...]] = [
        (
            data.report_date,
            data.kpi.red_count,
            data.kpi.orange_count,
            data.kpi.yellow_count,
            data.kpi.green_count,
            data.kpi.total_gap_qty,
            data.kpi.red_orange_pct,
        )
    ]
    if data.comparison.previous_date is not None:
        rows.append(
            (
                data.comparison.previous_date,
                data.comparison.red_change,
                data.comparison.orange_change,
            )
        )
    rows.extend(
        (
            item.material_pn,
            item.material_name,
            item.doi_days,
            item.gap_qty,
            item.gap_date,
            item.risk_reasons,
            item.supplier_concentration,
            item.min_lt,
            item.primary_supplier_id,
        )
        for item in data.top_risks
    )
    rows.extend(
        (
            item.supplier_id,
            item.supplier_name,
            item.red_orange_material_count,
            item.weighted_gap_qty,
        )
        for item in data.supplier_exposure
    )
    rows.extend(
        (
            item.commodity,
            item.red_count,
            item.orange_count,
            item.yellow_count,
            item.green_count,
            item.total_gap_qty,
        )
        for item in data.commodity_distribution
    )
    # explain_risk's deterministic HIGH_CONCENTRATION sentence cites the documented
    # 1.5x lead-time threshold, so include that rule constant in full-report evidence.
    rows.append((Decimal("1.5"),))
    return SafeResult(
        ok=True,
        columns=["report_evidence"],
        rows=rows,
        row_count=len(rows),
        final_sql=None,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _narrative_messages(section: str, data: ReportData) -> list[dict[str, str]]:
    section_instruction = (
        "用一段话概括当前风险整体状况与环比。"
        if section == OVERVIEW_SECTION
        else "用一段话给出下周需优先关注的物料、供应商或品类。"
    )
    payload = json.dumps(_json_safe(asdict(data)), ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": (
                "你是 ChainPilot 供应风险周报撰写助手。只输出一段简短中文，"
                "只能引用给定 JSON 中的数字和事实；不许计算新数字，"
                "不要给出任何自行合计或计数得到的数字；"
                "不要在叙述中使用内部规则码（如 GAP_BEFORE_LT、LOW_DOI），"
                "风险原因用通俗中文表述；不许推测，"
                "不许使用数据之外的建议依据。"
            ),
        },
        {"role": "user", "content": f"{section_instruction}\n结构化数据：{payload}"},
    ]


def _add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
    )


def _change_text(value: int | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"+{value}"
    return str(value)


def _overview_fallback(data: ReportData) -> str:
    text = (
        f"截至 {data.report_date.isoformat()}，红色风险 {data.kpi.red_count} 个、"
        f"橙色风险 {data.kpi.orange_count} 个，红橙物料占比 "
        f"{data.kpi.red_orange_pct:.2f}%，预计总缺口 {data.kpi.total_gap_qty:,}。"
    )
    if data.comparison.previous_date is None:
        return text + "暂无上一评估日，无法计算环比。"
    return (
        text
        + f"较 {data.comparison.previous_date.isoformat()}，红色风险变化 "
        f"{_change_text(data.comparison.red_change)} 个，橙色风险变化 "
        f"{_change_text(data.comparison.orange_change)} 个。"
    )


def _recommendations_fallback(data: ReportData) -> str:
    return (
        f"下周优先跟进 {data.kpi.red_count} 个红色风险物料和 "
        f"{data.kpi.orange_count} 个橙色风险物料，重点核查预计总缺口 "
        f"{data.kpi.total_gap_qty:,} 及对应的在途到货。"
    )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format_decimal(value: Decimal, places: int = 2) -> str:
    quantizer = Decimal(1).scaleb(-places)
    rounded = value.quantize(quantizer, rounding=ROUND_HALF_UP)
    return f"{rounded:,.{places}f}"


def _risk_explanation(item: TopRiskMaterial) -> str:
    row = {
        "material_pn": item.material_pn,
        "doi_days": float(item.doi_days),
        "gap_qty": item.gap_qty,
        "gap_date": item.gap_date,
        "risk_reasons": item.risk_reasons,
        "supplier_concentration": float(item.supplier_concentration),
        "min_lt": item.min_lt,
        "primary_supplier_id": item.primary_supplier_id,
    }
    return explain_risk(row, [])


def render_report(data: ReportData, overview: str, recommendations: str) -> str:
    """Render the fixed Markdown skeleton; every table value comes from ``data``."""
    lines = [
        f"# ChainPilot 供应风险周报（{data.report_date.isoformat()}）",
        "",
        "## 一、本周概述",
        "",
        overview,
        "",
        "## 二、KPI 总览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 红色风险物料 | {data.kpi.red_count} |",
        f"| 橙色风险物料 | {data.kpi.orange_count} |",
        f"| 黄色风险物料 | {data.kpi.yellow_count} |",
        f"| 绿色风险物料 | {data.kpi.green_count} |",
        f"| 预计总缺口量 | {data.kpi.total_gap_qty:,} |",
        f"| 红橙物料占比 | {data.kpi.red_orange_pct:.2f}% |",
        f"| 红色风险环比 | {_change_text(data.comparison.red_change)} |",
        f"| 橙色风险环比 | {_change_text(data.comparison.orange_change)} |",
        "",
        "## 三、Top 风险物料",
        "",
        "| 物料料号 | 物料名称 | DOI（天） | 缺口量 | 断料日 | 风险原因 |",
        "|---|---|---:|---:|---|---|",
    ]
    if data.top_risks:
        for item in data.top_risks:
            lines.append(
                f"| {_markdown_cell(item.material_pn)} | {_markdown_cell(item.material_name)} | "
                f"{_format_decimal(item.doi_days, 1)} | {item.gap_qty:,} | "
                f"{item.gap_date.isoformat() if item.gap_date else '—'} | "
                f"{_markdown_cell(item.risk_reasons or '—')} |"
            )
        lines.extend(["", "风险说明："])
        lines.extend(f"- {_risk_explanation(item)}" for item in data.top_risks)
    else:
        lines.append("| — | — | — | — | — | 暂无红色风险物料 |")

    lines.extend(
        [
            "",
            "## 四、供应商敞口",
            "",
            "| 供应商 ID | 供应商名称 | 关联红橙物料数 | 加权缺口量 |",
            "|---|---|---:|---:|",
        ]
    )
    if data.supplier_exposure:
        lines.extend(
            f"| {_markdown_cell(item.supplier_id)} | {_markdown_cell(item.supplier_name)} | "
            f"{item.red_orange_material_count} | {_format_decimal(item.weighted_gap_qty)} |"
            for item in data.supplier_exposure
        )
    else:
        lines.append("| — | — | — | — |")

    lines.extend(
        [
            "",
            "## 五、commodity 分布",
            "",
            "| Commodity | 红 | 橙 | 黄 | 绿 | 总缺口量 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    if data.commodity_distribution:
        lines.extend(
            f"| {_markdown_cell(item.commodity)} | {item.red_count} | {item.orange_count} | "
            f"{item.yellow_count} | {item.green_count} | {item.total_gap_qty:,} |"
            for item in data.commodity_distribution
        )
    else:
        lines.append("| — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## 六、下周关注建议",
            "",
            recommendations,
            "",
        ]
    )
    return "\n".join(lines)


def _guarded_narratives(
    data: ReportData, llm: ReportLLM | None
) -> tuple[str, str, list[str], TokenUsage]:
    overview_fallback = _overview_fallback(data)
    recommendations_fallback = _recommendations_fallback(data)
    if llm is None:
        return (
            overview_fallback,
            recommendations_fallback,
            list(ALL_NARRATIVE_SECTIONS),
            ZERO_USAGE,
        )

    usage = ZERO_USAGE
    try:
        overview_result = llm.chat(
            _narrative_messages(OVERVIEW_SECTION, data), temperature=0.0, timeout=30
        )
        usage = _add_usage(usage, overview_result.usage)
        recommendations_result = llm.chat(
            _narrative_messages(RECOMMENDATIONS_SECTION, data),
            temperature=0.0,
            timeout=30,
        )
        usage = _add_usage(usage, recommendations_result.usage)
    except Exception:
        return (
            overview_fallback,
            recommendations_fallback,
            list(ALL_NARRATIVE_SECTIONS),
            usage,
        )

    evidence = build_report_evidence(data)
    overview = overview_result.content.strip()
    recommendations = recommendations_result.content.strip()
    fallbacks: list[str] = []
    if (
        not overview
        or verify_answer(overview, evidence, "生成周报本周概述").verdict
        == "fail"
    ):
        overview = overview_fallback
        fallbacks.append(OVERVIEW_SECTION)
    if (
        not recommendations
        or verify_answer(
            recommendations, evidence, "生成周报下周关注建议"
        ).verdict
        == "fail"
    ):
        recommendations = recommendations_fallback
        fallbacks.append(RECOMMENDATIONS_SECTION)
    return overview, recommendations, fallbacks, usage


def _store_report(connection: duckdb.DuckDBPyConnection, result: ReportResult) -> None:
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS weekly_report ("
            "report_date DATE PRIMARY KEY, content_md VARCHAR, "
            "narrative_fallbacks VARCHAR, created_at TIMESTAMP)"
        )
        connection.execute(
            "DELETE FROM weekly_report WHERE report_date = ?", [result.report_date]
        )
        connection.execute(
            "INSERT INTO weekly_report VALUES (?, ?, ?, current_timestamp)",
            [
                result.report_date,
                result.content_md,
                json.dumps(result.narrative_fallbacks, ensure_ascii=False),
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def generate_report(
    llm: ReportLLM | None = None,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
    db_path: str | Path | None = None,
) -> ReportResult:
    """Generate and persist a weekly report; narrative failures always degrade safely."""
    owns_connection = connection is None
    if connection is None:
        connection = duckdb.connect(str(db_path or database_path()))
    try:
        data = assemble_report_data(connection)
        overview, recommendations, fallbacks, usage = _guarded_narratives(data, llm)
        result = ReportResult(
            report_date=data.report_date,
            content_md=render_report(data, overview, recommendations),
            narrative_fallbacks=fallbacks,
            usage=usage,
        )
        _store_report(connection, result)
        return result
    finally:
        if owns_connection:
            connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the ChainPilot weekly risk report")
    parser.add_argument(
        "--no-llm", action="store_true", help="use deterministic narrative fallbacks"
    )
    args = parser.parse_args(argv)
    llm = None if args.no_llm else DeepSeekClient()
    result = generate_report(llm)
    print(result.content_md)
    print("降级段落：" + (", ".join(result.narrative_fallbacks) or "无"))
    print(
        f"Tokens: prompt={result.usage.prompt_tokens}, "
        f"completion={result.usage.completion_tokens}, total={result.usage.total_tokens}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
