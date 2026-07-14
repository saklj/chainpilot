"""Offline tests for deterministic evidence checks and the assembled chat chain."""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal

import pytest

import agent.chat as chat_module
from agent.chat import (
    EMPTY_RESULT_ANSWER,
    GUARDRAIL_FAILED_ANSWER,
    OUT_OF_SCOPE_ANSWER,
    answer_question,
)
from agent.guardrail import verify_answer
from agent.llm import DeepSeekClient, LLMResult, TokenUsage
from agent.safe_sql import SafeResult


class SequenceLLM:
    """Return distinct mock completions in call order and retain every prompt."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, float, float]] = []

    def chat(self, messages, *, temperature=0.0, timeout=30):
        self.calls.append((messages, temperature, timeout))
        content = self.responses.pop(0)
        return LLMResult(content, TokenUsage(prompt_tokens=10, completion_tokens=2))


def result(*rows, columns=None) -> SafeResult:
    return SafeResult(
        ok=True,
        columns=columns or [f"column_{index}" for index in range(len(rows[0]) if rows else 1)],
        rows=list(rows),
        row_count=len(rows),
        final_sql="SELECT evidence LIMIT 200",
    )


@pytest.mark.parametrize(
    ("answer", "cell", "display"),
    [
        ("总量是13,153。", 13153, "13153"),
        ("总量是13，153.5。", Decimal("13153.5"), "13153.5"),
        ("变动为-13,153。", -13153, "-13153"),
        ("波动范围为±13，153。", 13153, "13153"),
    ],
)
def test_guardrail_extracts_number_formats(answer: str, cell, display: str) -> None:
    verdict = verify_answer(answer, result((cell,)), "查询总量")
    assert verdict.verdict == "pass"
    assert verdict.matched[display] == (0, 0)
    assert verdict.checked_count == 1


@pytest.mark.parametrize("cell", [Decimal("35.5"), Decimal("0.355")])
def test_guardrail_percentage_tries_both_conventions(cell: Decimal) -> None:
    verdict = verify_answer("占比为35.5%。", result((cell,)), "查询占比")
    assert verdict.verdict == "pass"
    assert verdict.matched == {"35.5%": (0, 0)}


@pytest.mark.parametrize("answer", ["日期是2016-05-25。", "日期是2016年5月25日。"])
def test_guardrail_normalizes_dates(answer: str) -> None:
    verdict = verify_answer(answer, result((date(2016, 5, 25),)), "查询日期")
    assert verdict.verdict == "pass"
    assert verdict.matched == {"2016-05-25": (0, 0)}


def test_guardrail_exempts_identifier_numbers() -> None:
    answer = "物料PN-00003由SUP-013供应，影响FOODS_3_090。"
    verdict = verify_answer(answer, result(("RED",)), "查询物料")
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


def test_guardrail_masks_exact_descriptive_result_string() -> None:
    verdict = verify_answer(
        "高风险物料包括 Carton 271。",
        result(("Carton 271",)),
        "高风险物料有哪些？",
    )
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0
    assert verdict.unmatched == []


def test_guardrail_does_not_derive_numeric_evidence_from_descriptive_string() -> None:
    verdict = verify_answer(
        "该物料的数量是271。",
        result(("Carton 271",)),
        "该物料的数量是多少？",
    )
    assert verdict.verdict == "fail"
    assert verdict.checked_count == 1
    assert verdict.unmatched == ["271"]


def test_guardrail_does_not_mask_a_longer_unreturned_name() -> None:
    verdict = verify_answer(
        "高风险物料包括 Carton 2711。",
        result(("Carton 271",)),
        "高风险物料有哪些？",
    )
    assert verdict.verdict == "fail"
    assert verdict.unmatched == ["2711"]


def test_guardrail_red_risk_material_name_regression() -> None:
    safe_result = result(
        ("PN-00271", "Carton 271", "RED"),
        ("PN-00214", "Case Label 214", "RED"),
        ("PN-00046", "Carton 46", "RED"),
        columns=["material_pn", "material_name", "risk_level"],
    )
    verdict = verify_answer(
        "当前共3个红色风险物料：PN-00271 Carton 271、"
        "PN-00214 Case Label 214、PN-00046 Carton 46。",
        safe_result,
        "当前哪些物料是红色风险？",
    )
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0
    assert verdict.unmatched == []


def test_guardrail_exempts_numbers_from_question() -> None:
    verdict = verify_answer("已列出前5名。", result(("A",)), "请列出前5名")
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


def test_guardrail_exempts_numeric_components_of_question_identifiers() -> None:
    verdict = verify_answer(
        "问题中的编号数字是3。",
        result(("RED",)),
        "请查询PN-00003的风险。",
    )
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


def test_guardrail_exempts_row_count_ordinals_and_returned_year() -> None:
    safe_result = result(
        (date(2016, 5, 25), "A"),
        (date(2016, 5, 26), "B"),
    )
    answer = "共2行，第1行和第2行都属于2016年。"
    verdict = verify_answer(answer, safe_result, "查询数据")
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


@pytest.mark.parametrize("ordinal", ["1. 第一项", "**1.** 第一项", "1）第一项"])
def test_guardrail_exempts_supported_list_ordinals(ordinal: str) -> None:
    verdict = verify_answer(ordinal, result(("A",), ("B",)), "查询数据")
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


def test_guardrail_exempts_bounded_counts_with_quantifiers() -> None:
    verdict = verify_answer("其余41个物料也需关注。", result(*(("A",),) * 90), "查询数据")
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


def test_guardrail_rejects_count_above_row_count() -> None:
    verdict = verify_answer("共有3个物料。", result(("A",), ("B",)), "查询数据")
    assert verdict.verdict == "fail"
    assert verdict.unmatched == ["3"]


@pytest.mark.parametrize("answer", ["缺口大于 0。", "缺口 > 0。", "缺口不为0。"])
def test_guardrail_exempts_literal_zero_comparison(answer: str) -> None:
    verdict = verify_answer(answer, result(("A",)), "查询数据")
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


def test_guardrail_exempts_numbers_and_dates_from_owned_prompt_text() -> None:
    verdict = verify_answer(
        "按未来28天需求计算，截止日期为2016-05-22。",
        result(("A",)),
        "解释口径",
        exempt_text="公式使用未来28天需求，截止日期为2016-05-22。",
    )
    assert verdict.verdict == "pass"
    assert verdict.checked_count == 0


def test_guardrail_rejects_number_outside_owned_prompt_text() -> None:
    verdict = verify_answer(
        "按未来28天需求计算，结果是999。",
        result(("A",)),
        "解释口径",
        exempt_text="公式使用未来28天需求。",
    )
    assert verdict.verdict == "fail"
    assert verdict.unmatched == ["999"]


def test_guardrail_still_rejects_unqualified_zero_and_body_number() -> None:
    verdict = verify_answer("库存是0，另有999件。", result(("A",)), "查询数据")
    assert verdict.verdict == "fail"
    assert verdict.unmatched == ["0", "999"]


def test_guardrail_all_evidence_matches() -> None:
    safe_result = result((13153, Decimal("0.355"), date(2016, 5, 25)))
    verdict = verify_answer(
        "总量约13,150，占比35.5%，日期2016年5月25日。",
        safe_result,
        "查询结果",
    )
    assert verdict.verdict == "pass"
    assert verdict.unmatched == []
    assert verdict.checked_count == 3


def test_guardrail_catches_injected_hallucination_exactly() -> None:
    verdict = verify_answer("总量是13,253。", result((13153,)), "查询总量")
    assert verdict.verdict == "fail"
    assert verdict.matched == {}
    assert verdict.unmatched == ["13253"]
    assert verdict.checked_count == 1


def test_guardrail_rounding_tolerance_boundary() -> None:
    close = verify_answer("总量约13,150。", result((13153,)), "查询总量")
    far = verify_answer("总量约13,020。", result((13153,)), "查询总量")
    assert close.verdict == "pass"
    assert far.verdict == "fail"
    assert far.unmatched == ["13020"]


def test_chat_normal_two_call_path_passes_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    safe_result = result(
        (13153, date(2016, 5, 25)), columns=["total_gap", "eval_date"]
    )
    monkeypatch.setattr(chat_module, "execute_safe", lambda _: safe_result)
    llm = SequenceLLM(
        "```sql\nSELECT sum(gap_qty) AS total_gap, max(eval_date) AS eval_date "
        "FROM material_risk\n```",
        "总缺口约13,150，评估日期为2016年5月25日。",
    )
    response = answer_question("当前总缺口是多少？", llm)
    assert not response.refused
    assert response.refusal_reason is None
    assert response.verdict is not None and response.verdict.verdict == "pass"
    assert response.usage == TokenUsage(20, 4)
    assert response.rows == [[13153, "2016-05-25"]]
    assert len(llm.calls) == 2
    assert all(call[1:] == (0.0, 30) for call in llm.calls)


def test_chat_punctuated_no_answer_refuses_after_one_call() -> None:
    llm = SequenceLLM("  NO_ANSWER。 \n")
    response = answer_question("员工工资是多少？", llm)
    assert response.refused
    assert response.refusal_reason == "out_of_scope"
    assert response.answer == OUT_OF_SCOPE_ANSWER
    assert len(llm.calls) == 1


def test_chat_invalid_generation_refuses_without_retry() -> None:
    llm = SequenceLLM("SELECT * FROM materials")
    response = answer_question("列出物料", llm)
    assert response.refused
    assert response.refusal_reason == "generation_failed"
    assert len(llm.calls) == 1


def test_chat_rejected_sql_carries_structured_reason() -> None:
    llm = SequenceLLM("```sql\nDROP TABLE materials\n```")
    response = answer_question("删除物料", llm)
    assert response.refused
    assert response.refusal_reason == "sql_rejected"
    assert "not_select" in response.answer
    assert response.sql == "DROP TABLE materials"
    assert len(llm.calls) == 1


def test_chat_empty_result_skips_answer_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    safe_result = SafeResult(
        ok=True,
        columns=["material_pn"],
        rows=[],
        row_count=0,
        final_sql="SELECT material_pn FROM materials WHERE false LIMIT 200",
    )
    monkeypatch.setattr(chat_module, "execute_safe", lambda _: safe_result)
    llm = SequenceLLM("```sql\nSELECT material_pn FROM materials WHERE false\n```")
    response = answer_question("查不存在的物料", llm)
    assert not response.refused
    assert response.answer == EMPTY_RESULT_ANSWER
    assert response.verdict is None
    assert len(llm.calls) == 1


def test_chat_blocks_hallucinated_answer_and_keeps_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_module, "execute_safe", lambda _: result((13153,)))
    llm = SequenceLLM(
        "```sql\nSELECT sum(gap_qty) FROM material_risk\n```",
        "总缺口是13,253。",
    )
    response = answer_question("总缺口是多少？", llm)
    assert response.refused
    assert response.refusal_reason == "guardrail_failed"
    assert response.answer == GUARDRAIL_FAILED_ANSWER
    assert response.draft_answer == "总缺口是13,253。"
    assert response.verdict is not None
    assert response.verdict.unmatched == ["13253"]


def test_answer_prompt_truncates_to_50_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [(f"PN-{index:05d}",) for index in range(60)]
    safe_result = result(*rows, columns=["material_pn"])
    monkeypatch.setattr(chat_module, "execute_safe", lambda _: safe_result)
    llm = SequenceLLM(
        "```sql\nSELECT material_pn FROM materials\n```",
        "查询返回了结果。",
    )
    response = answer_question("列出物料", llm)
    second_user_message = llm.calls[1][0][1]["content"]
    assert "仅提供前 50 行" in second_user_message
    assert "PN-00049" in second_user_message
    assert "PN-00050" not in second_user_message
    assert response.row_count == 60


def test_chat_response_is_json_serializable(monkeypatch: pytest.MonkeyPatch) -> None:
    safe_result = result(
        (Decimal("13153.5"), date(2016, 5, 25)), columns=["quantity", "date"]
    )
    monkeypatch.setattr(chat_module, "execute_safe", lambda _: safe_result)
    llm = SequenceLLM(
        "```sql\nSELECT 13153.5 AS quantity, DATE '2016-05-25' AS date\n```",
        "数量为13,153.5，日期为2016-05-25。",
    )
    response = answer_question("查询数量和日期", llm)
    encoded = json.dumps(response.to_dict(), ensure_ascii=False)
    assert '"question"' in encoded
    assert '"verdict": "pass"' in encoded
    assert response.rows == [[13153.5, "2016-05-25"]]


@pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY is not configured"
)
@pytest.mark.parametrize(
    "question",
    ["当前哪些物料是红色风险？", "按 commodity 汇总最新风险。"],
)
def test_real_chat_smoke(question: str) -> None:
    response = answer_question(question, DeepSeekClient())
    assert not response.refused
    assert response.verdict is not None and response.verdict.verdict == "pass"


def test_guardrail_matches_month_day_date_against_evidence() -> None:
    verdict = verify_answer(
        "预计 5月25日 断料，缺口 434。",
        result((434, date(2016, 5, 25))),
        "什么时候断料？",
    )
    assert verdict.verdict == "pass"
    assert verdict.matched["5月25日"] == (0, 1)


def test_guardrail_rejects_month_day_absent_from_evidence() -> None:
    verdict = verify_answer(
        "预计 7月1日 断料。",
        result((434, date(2016, 5, 25))),
        "什么时候断料？",
    )
    assert verdict.verdict == "fail"
    assert verdict.unmatched == ["7月1日"]


def test_guardrail_exempts_top_n_presentation_counts() -> None:
    verdict = verify_answer(
        "前 3 名如下；Top 2 的缺口都超过 400。",
        result((434,), (410,), (300,)),
        "看看缺口排名",
    )
    assert verdict.verdict == "fail"  # 400 是编造阈值，仍须拦
    assert verdict.unmatched == ["400"]
    assert "3" not in verdict.unmatched and "2" not in verdict.unmatched


def test_guardrail_top_n_count_beyond_rows_still_fails() -> None:
    verdict = verify_answer(
        "前 9 名如下。",
        result((434,), (410,)),
        "看看缺口排名",
    )
    assert verdict.verdict == "fail"
    assert verdict.unmatched == ["9"]


def test_guardrail_matches_dash_month_day_and_result_identifier_digits() -> None:
    verdict = verify_answer(
        "SUP-040（供应商 40）预计 05-25 断料。",
        result(("SUP-040", date(2016, 5, 25))),
        "谁会断料？",
    )
    assert verdict.verdict == "pass"


def test_guardrail_still_rejects_bare_digits_without_identifier_cell() -> None:
    verdict = verify_answer(
        "大约有 40 件缺口。",
        result(("Carton 271", 434)),
        "缺口多少？",
    )
    assert verdict.verdict == "fail"
    assert verdict.unmatched == ["40"]


def test_guardrail_matches_slash_month_day_and_spaced_quantifier() -> None:
    verdict = verify_answer(
        "预计 5/25 断料，共 2 个物料受影响。",
        result(("PN-00003", date(2016, 5, 25)), ("PN-00001", date(2016, 5, 25))),
        "断料情况？",
    )
    assert verdict.verdict == "pass"
