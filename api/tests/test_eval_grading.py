"""Deterministic grading, eval validation, and M4-T4 prompt regressions."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.run_eval import (  # noqa: E402
    QUESTIONS_PATH,
    grade_response,
    validate_file,
)

import agent.chat as chat_module  # noqa: E402
from agent.chat import ChatResponse, answer_question  # noqa: E402
from agent.glossary import load_glossary  # noqa: E402
from agent.guardrail import GuardrailVerdict  # noqa: E402
from agent.llm import LLMResult, TokenUsage  # noqa: E402
from agent.safe_sql import SafeResult  # noqa: E402


def response(
    rows: list[list[object]] | None = None,
    *,
    refused: bool = False,
    answer: str = "",
) -> ChatResponse:
    values = rows or []
    width = max((len(row) for row in values), default=0)
    return ChatResponse(
        question="question",
        answer=answer,
        refused=refused,
        refusal_reason="out_of_scope" if refused else None,
        sql=None,
        final_sql=None,
        columns=[f"column_{index}" for index in range(width)],
        rows=values,
        row_count=len(values),
        verdict=GuardrailVerdict("pass", {}, [], 0) if not refused else None,
        draft_answer=None,
        usage=TokenUsage(),
    )


def test_exact_set_matches_any_response_column() -> None:
    gold = [("PN-A",), ("PN-B",)]
    matched = grade_response(
        response([[1, "PN-A"], [2, "PN-B"]]), {"type": "exact_set"}, gold
    )
    mismatched = grade_response(
        response([[1, "PN-A"], [2, "PN-C"]]), {"type": "exact_set"}, gold
    )
    assert matched.passed
    assert not mismatched.passed


def test_superset_accepts_extra_values_but_rejects_missing_gold() -> None:
    gold = [("RED-A",), ("RED-B",)]
    superset = grade_response(
        response([["RED-A"], ["RED-B"], ["ORANGE-C"]]),
        {"type": "superset"},
        gold,
    )
    missing = grade_response(
        response([["RED-A"]]), {"type": "superset"}, gold
    )
    assert superset.passed
    assert not missing.passed


def test_contains_values_uses_half_percent_numeric_tolerance() -> None:
    gold = [(13153, "RED")]
    close = grade_response(
        response([["RED", 13150]]), {"type": "contains_values"}, gold
    )
    far = grade_response(
        response([["RED", 13000]]), {"type": "contains_values"}, gold
    )
    assert close.passed
    assert not far.passed
    assert "13153" in far.reason


def test_refusal_check_only_requires_refused_flag() -> None:
    assert grade_response(response(refused=True), {"type": "refusal"}).passed
    assert not grade_response(response(), {"type": "refusal"}).passed


def test_answer_keywords_requires_all_and_rejects_forbidden() -> None:
    check = {
        "type": "answer_keywords",
        "keywords": ["库存", "缺料"],
        "forbidden": ["缺料天数"],
    }
    clean = grade_response(response(answer="DOI 表示库存可支撑天数，可用于看缺料。"), check)
    forbidden = grade_response(response(answer="DOI 是缺料天数，与库存和缺料有关。"), check)
    missing = grade_response(response(answer="DOI 是一个指标。"), check)
    assert clean.passed
    assert not forbidden.passed and "forbidden" in forbidden.reason
    assert not missing.passed and "missing" in missing.reason


def test_answer_keywords_can_also_require_result_superset() -> None:
    check = {
        "type": "answer_keywords",
        "keywords": ["库存"],
        "also_superset_gold": "SELECT material_pn FROM material_risk",
    }
    gold = [("PN-A",), ("PN-B",)]
    passed = grade_response(
        response([["PN-A"], ["PN-B"], ["PN-C"]], answer="DOI 表示库存天数。"),
        check,
        also_gold_rows=gold,
    )
    failed = grade_response(
        response([["PN-A"]], answer="DOI 表示库存天数。"),
        check,
        also_gold_rows=gold,
    )
    assert passed.passed
    assert not failed.passed


def test_validate_reports_missing_fields_counts_and_bad_gold_sql(tmp_path: Path) -> None:
    database = tmp_path / "eval.duckdb"
    connection = duckdb.connect(str(database))
    connection.close()
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text(
        '{"id":"BAD1","category":"template","question":"x","check":{"type":"exact_set"}}\n'
        '{"id":"BAD2","category":"template","question":"x",'
        '"check":{"type":"exact_set"},"gold_sql":"SELECT * FROM missing_table",'
        '"source":"designed","note":"bad sql"}\n',
        encoding="utf-8",
    )
    errors = validate_file(bad_file, db_path=database)
    assert any("missing_fields" in error for error in errors)
    assert any("category_count template" in error for error in errors)
    assert any("execution_error" in error for error in errors)


def test_real_50_question_file_validates_without_llm() -> None:
    assert validate_file(QUESTIONS_PATH) == []


def test_chat_answer_prompt_contains_glossary_and_no_self_count_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_result = SafeResult(
        ok=True,
        columns=["material_pn", "gap_qty"],
        rows=[("PN-00003", 13153)],
        row_count=1,
        final_sql="SELECT material_pn, gap_qty FROM material_risk LIMIT 200",
    )
    monkeypatch.setattr(chat_module, "execute_safe", lambda _: safe_result)

    class PromptLLM:
        def __init__(self) -> None:
            self.calls: list[object] = []
            self.responses = iter(
                [
                    "```sql\nSELECT material_pn, gap_qty FROM material_risk\n```",
                    "DOI 表示现有库存可支撑的天数，PN-00003 有缺料风险。",
                ]
            )

        def chat(self, messages, *, temperature=0.0, timeout=30):
            self.calls.append(messages)
            return LLMResult(next(self.responses), TokenUsage())

    llm = PromptLLM()
    result = answer_question("DOI 是什么意思，我现在想看缺料。", llm)
    assert not result.refused
    second_system = llm.calls[1][0]["content"]
    terms = load_glossary()
    assert len(terms) >= 10
    assert all(term.term in second_system for term in terms)
    assert "术语表定义为准" in second_system
    assert "自行合计、计数" in second_system


def test_exact_set_accepts_any_gold_column_as_equivalent_key() -> None:
    gold = [("SUP-001", "Alpha Supply"), ("SUP-002", "Beta Supply")]
    by_name = grade_response(
        response([["Alpha Supply", 10], ["Beta Supply", 20]]),
        {"type": "exact_set"},
        gold,
    )
    assert by_name.passed
    neither = grade_response(
        response([["Gamma Supply", 10], ["Beta Supply", 20]]),
        {"type": "exact_set"},
        gold,
    )
    assert not neither.passed


def test_contains_values_skips_entities_named_in_question() -> None:
    import dataclasses

    base = response([["2016-05-21", 20]])
    asked = dataclasses.replace(base, question="PN-00003 最新库存快照日期和现有量是多少？")
    grade = grade_response(
        asked, {"type": "contains_values"}, [("PN-00003", "2016-05-21", 20)]
    )
    assert grade.passed, grade.reason
    missing_fact = grade_response(
        dataclasses.replace(base, question="PN-00003 现有量？"),
        {"type": "contains_values"},
        [("PN-00003", 99)],
    )
    assert not missing_fact.passed
