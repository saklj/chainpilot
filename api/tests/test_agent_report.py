"""Offline tests for deterministic weekly-report generation and guarded narratives."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from agent.guardrail import verify_answer
from agent.llm import DeepSeekClient, LLMResult, TokenUsage
from agent.report import (
    ALL_NARRATIVE_SECTIONS,
    OVERVIEW_SECTION,
    assemble_report_data,
    build_report_evidence,
    database_path,
    generate_report,
)


class SequenceLLM:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[object, float, float]] = []

    def chat(self, messages, *, temperature=0.0, timeout=30):
        self.calls.append((messages, temperature, timeout))
        return LLMResult(next(self.responses), TokenUsage(10, 2))


class FailingLLM:
    def chat(self, messages, *, temperature=0.0, timeout=30):
        raise RuntimeError("offline")


@pytest.fixture
def report_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE material_risk ("
        "material_pn VARCHAR, eval_date DATE, doi_days DECIMAL(6,1), "
        "lt_coverage DECIMAL(5,2), supplier_concentration DECIMAL(5,2), "
        "gap_qty INTEGER, gap_date DATE, risk_level VARCHAR, risk_reasons VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE materials (material_pn VARCHAR, material_name VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE supply_split (material_pn VARCHAR, supplier_id VARCHAR, "
        "split_pct DECIMAL(5,2), lead_time_days INTEGER)"
    )
    connection.execute(
        "CREATE TABLE v_risk_by_supplier ("
        "eval_date DATE, supplier_id VARCHAR, supplier_name VARCHAR, "
        "red_orange_material_count INTEGER, weighted_gap_qty DECIMAL(12,2))"
    )
    connection.execute(
        "CREATE TABLE v_risk_by_commodity ("
        "eval_date DATE, commodity VARCHAR, red_count INTEGER, orange_count INTEGER, "
        "yellow_count INTEGER, green_count INTEGER, total_gap_qty INTEGER)"
    )
    connection.execute(
        "INSERT INTO materials VALUES "
        "('PN-00101', 'Carton 101'), ('PN-00202', 'Case Label 202'), "
        "('PN-00303', 'Food Base 303'), ('PN-00404', 'Container 404')"
    )
    connection.execute(
        "INSERT INTO supply_split VALUES "
        "('PN-00101', 'SUP-001', 100, 10), "
        "('PN-00202', 'SUP-002', 80, 12), "
        "('PN-00202', 'SUP-003', 20, 20), "
        "('PN-00303', 'SUP-004', 100, 15), "
        "('PN-00404', 'SUP-005', 100, 20)"
    )
    connection.execute(
        "INSERT INTO material_risk VALUES "
        "('PN-00101', '2024-01-01', 8, 0.8, 100, 10, '2024-01-20', "
        " 'ORANGE', 'GAP_IN_HORIZON'), "
        "('PN-00202', '2024-01-01', 15, 1.2, 80, 0, NULL, 'YELLOW', 'LOW_DOI'), "
        "('PN-00303', '2024-01-01', 9, 0.6, 100, 20, '2024-01-18', "
        " 'ORANGE', 'GAP_IN_HORIZON'), "
        "('PN-00404', '2024-01-01', 30, 1.5, 100, 0, NULL, 'GREEN', ''), "
        "('PN-00101', '2024-01-08', 2, 0.2, 100, 1000, '2024-01-10', "
        " 'RED', 'GAP_BEFORE_LT;LOW_DOI'), "
        "('PN-00202', '2024-01-08', 3, 0.25, 80, 500, '2024-01-12', "
        " 'RED', 'GAP_BEFORE_LT;LOW_DOI'), "
        "('PN-00303', '2024-01-08', 7, 0.47, 100, 200, '2024-01-25', "
        " 'ORANGE', 'GAP_IN_HORIZON'), "
        "('PN-00404', '2024-01-08', 30, 1.5, 100, 0, NULL, 'GREEN', '')"
    )
    connection.execute(
        "INSERT INTO v_risk_by_supplier VALUES "
        "('2024-01-08', 'SUP-001', 'North Supply 1', 2, 900), "
        "('2024-01-08', 'SUP-002', 'South Supply 2', 1, 100), "
        "('2024-01-01', 'SUP-001', 'North Supply 1', 1, 10)"
    )
    connection.execute(
        "INSERT INTO v_risk_by_commodity VALUES "
        "('2024-01-08', 'PACKAGING', 2, 0, 0, 0, 1500), "
        "('2024-01-08', 'RAW_FOOD', 0, 1, 0, 1, 200), "
        "('2024-01-01', 'PACKAGING', 0, 1, 1, 0, 10)"
    )
    yield connection
    connection.close()


def test_data_assembly_values_and_order(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    data = assemble_report_data(report_connection)
    assert data.report_date == date(2024, 1, 8)
    assert data.kpi.red_count == 2
    assert data.kpi.orange_count == 1
    assert data.kpi.yellow_count == 0
    assert data.kpi.green_count == 1
    assert data.kpi.total_gap_qty == 1700
    assert data.kpi.red_orange_pct == Decimal("75.00")
    assert data.comparison.previous_date == date(2024, 1, 1)
    assert data.comparison.red_change == 2
    assert data.comparison.orange_change == -1
    assert [item.material_pn for item in data.top_risks] == ["PN-00101", "PN-00202"]
    assert [item.gap_qty for item in data.top_risks] == [1000, 500]
    assert [item.supplier_id for item in data.supplier_exposure] == ["SUP-001", "SUP-002"]
    assert data.supplier_exposure[0].weighted_gap_qty == Decimal("900.00")
    assert [item.commodity for item in data.commodity_distribution] == [
        "PACKAGING",
        "RAW_FOOD",
    ]


def test_no_llm_template_is_complete_and_evidence_backed(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    report = generate_report(connection=report_connection)
    for heading in (
        "# ChainPilot 供应风险周报（2024-01-08）",
        "## 一、本周概述",
        "## 二、KPI 总览",
        "## 三、Top 风险物料",
        "## 四、供应商敞口",
        "## 五、commodity 分布",
        "## 六、下周关注建议",
    ):
        assert heading in report.content_md
    assert report.narrative_fallbacks == ALL_NARRATIVE_SECTIONS
    assert "红色风险 2 个" in report.content_md
    assert "预计总缺口 1,700" in report.content_md
    assert "现在追料已来不及" in report.content_md
    assert "{{" not in report.content_md and "}}" not in report.content_md
    data = assemble_report_data(report_connection)
    verdict = verify_answer(
        report.content_md, build_report_evidence(data), "生成供应风险周报"
    )
    assert verdict.verdict == "pass", verdict.unmatched


def test_hallucinated_overview_falls_back_but_clean_recommendation_remains(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    llm = SequenceLLM(
        "本周红色风险上升到888个。",
        "下周建议优先跟进红色风险物料。",
    )
    report = generate_report(llm, connection=report_connection)
    assert report.narrative_fallbacks == [OVERVIEW_SECTION]
    assert "888" not in report.content_md
    assert "截至 2024-01-08" in report.content_md
    assert "下周建议优先跟进红色风险物料。" in report.content_md
    assert report.usage == TokenUsage(20, 4)
    assert len(llm.calls) == 2
    assert all(call[1:] == (0.0, 30) for call in llm.calls)


def test_clean_narratives_are_preserved(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    overview = "本周风险主要集中在 PACKAGING。"
    recommendations = "下周优先关注 North Supply 1。"
    report = generate_report(
        SequenceLLM(overview, recommendations), connection=report_connection
    )
    assert report.narrative_fallbacks == []
    assert overview in report.content_md
    assert recommendations in report.content_md


def test_empty_narrative_uses_section_fallback(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    report = generate_report(
        SequenceLLM("   ", "下周优先关注红色风险物料。"),
        connection=report_connection,
    )
    assert report.narrative_fallbacks == [OVERVIEW_SECTION]
    assert "截至 2024-01-08" in report.content_md


def test_llm_exception_falls_back_without_breaking_report(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    report = generate_report(FailingLLM(), connection=report_connection)
    assert report.narrative_fallbacks == ALL_NARRATIVE_SECTIONS
    assert report.usage == TokenUsage()
    assert "## 六、下周关注建议" in report.content_md


def test_single_eval_date_uses_dash_for_comparison(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    report_connection.execute("DELETE FROM material_risk WHERE eval_date = '2024-01-01'")
    data = assemble_report_data(report_connection)
    assert data.comparison.previous_date is None
    assert data.comparison.red_change is None
    report = generate_report(connection=report_connection)
    assert "| 红色风险环比 | — |" in report.content_md
    assert "暂无上一评估日" in report.content_md


def test_report_storage_is_idempotent(
    report_connection: duckdb.DuckDBPyConnection,
) -> None:
    first = generate_report(connection=report_connection)
    second = generate_report(connection=report_connection)
    stored = report_connection.execute(
        "SELECT report_date, content_md, narrative_fallbacks FROM weekly_report"
    ).fetchall()
    assert len(stored) == 1
    assert stored[0][0] == date(2024, 1, 8)
    assert stored[0][1] == first.content_md == second.content_md
    assert json.loads(stored[0][2]) == ALL_NARRATIVE_SECTIONS


@pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY is not configured"
)
def test_real_report_smoke() -> None:
    report = generate_report(DeepSeekClient())
    assert len(report.narrative_fallbacks) < 2
    connection = duckdb.connect(str(database_path()), read_only=True)
    try:
        data = assemble_report_data(connection)
    finally:
        connection.close()
    verdict = verify_answer(
        report.content_md, build_report_evidence(data), "生成供应风险周报"
    )
    assert verdict.verdict == "pass", verdict.unmatched
