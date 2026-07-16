"""Offline tests for the bounded diagnosis agent and deterministic workflow."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from agent.diagnose import (
    diagnose_material,
    get_forecast_error,
    get_po_status,
    get_risk_detail,
    get_shared_demand,
    query_sql,
)
from agent.diagnose_workflow import diagnose_material_workflow
from agent.llm import LLMResult, TokenUsage

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"


class SequenceLLM:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)

    def chat(self, messages, *, temperature=0.0, timeout=30):
        del messages, temperature, timeout
        return LLMResult(next(self.responses), TokenUsage(10, 2))


@pytest.fixture
def connection() -> duckdb.DuckDBPyConnection:
    if not REAL_DB.is_file():
        pytest.skip("real DuckDB fixture is unavailable")
    value = duckdb.connect(str(REAL_DB), read_only=True)
    yield value
    value.close()


def test_react_happy_path_and_guardrail(connection: duckdb.DuckDBPyConnection) -> None:
    llm = SequenceLLM(
        '{"thought":"查风险","action":"get_risk_detail","args":{"material_pn":"PN-00211"}}',
        '{"thought":"查在途","action":"get_po_status","args":{"material_pn":"PN-00211"}}',
        '{"action":"final","category":"single_source_supply","root_cause":"该料只有 1 家供应源，库存 20，在途 9，缺口 434。"}',
    )
    result = diagnose_material(llm, connection, "PN-00211")
    assert result.category == "single_source_supply"
    assert result.steps == 3
    assert len(result.trace) == 2
    assert result.guardrail_verdict == "pass"
    assert not result.degraded


def test_bounded_loop_bad_json_and_hallucination_fallback(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    action = '{"action":"get_forecast_error","args":{}}'
    bounded = diagnose_material(SequenceLLM(*([action] * 8)), connection, "PN-00211")
    assert bounded.category == "unknown"
    assert bounded.steps == 8
    assert len(bounded.trace) == 8
    assert "已排除" in bounded.root_cause

    retry = diagnose_material(
        SequenceLLM("not json", action, '{"action":"final","category":"unknown","root_cause":"预测模型 WMAPE 20.148。"}'),
        connection,
        "PN-00211",
    )
    assert retry.steps == 2
    broken = diagnose_material(SequenceLLM("bad", "still bad"), connection, "PN-00211")
    assert broken.category == "unknown" and broken.degraded

    fabricated = diagnose_material(
        SequenceLLM(
            '{"action":"get_risk_detail","args":{"material_pn":"PN-00211"}}',
            '{"action":"final","category":"single_source_supply","root_cause":"库存 999999，确定断供。"}',
        ),
        connection,
        "PN-00211",
    )
    assert fabricated.guardrail_verdict == "fail"
    assert fabricated.degraded
    assert "999999" not in fabricated.root_cause


def test_tools_and_workflow_hit_all_ground_truth(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    assert get_risk_detail(connection, "PN-00211").rows[0][5] == 1
    assert get_po_status(connection, "PN-00211").rows
    shared = get_shared_demand(connection, "PN-00001")
    assert shared.rows[0][3] > 1
    assert get_forecast_error(connection).rows
    assert query_sql(connection, "SELECT material_pn FROM materials LIMIT 1").rows

    mapping = {
        "单源+低库存+在途不足": "single_source_supply",
        "共用料高需求+库存薄": "shared_demand_competition",
        "长交期+零在途": "long_leadtime_no_po",
    }
    scenarios = json.loads((REPO_ROOT / "data" / "ground_truth_scenarios.json").read_text())
    actual = [
        diagnose_material_workflow(connection, item["material_pn"]).category
        for item in scenarios
    ]
    assert actual == [mapping[item["construction"]] for item in scenarios]
    with pytest.raises(ValueError, match="not found"):
        diagnose_material_workflow(connection, "PN-NOT-FOUND")
