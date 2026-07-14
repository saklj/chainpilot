"""Assemble NL-to-SQL, safe execution, answer generation, and evidence checks."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

if __package__:
    from .guardrail import GuardrailVerdict, verify_answer
    from .llm import DeepSeekClient, TokenUsage
    from .nl2sql import ChatLLM, generate_sql
    from .safe_sql import SafeResult, execute_safe
else:
    from guardrail import GuardrailVerdict, verify_answer
    from llm import DeepSeekClient, TokenUsage
    from nl2sql import ChatLLM, generate_sql
    from safe_sql import SafeResult, execute_safe

RefusalReason = Literal[
    "out_of_scope", "generation_failed", "sql_rejected", "guardrail_failed"
]

OUT_OF_SCOPE_ANSWER = "数据里没有这项信息，无法回答。"
GENERATION_FAILED_ANSWER = "未能生成可靠的查询，请换一种问法再试。"
EMPTY_RESULT_ANSWER = "查询执行成功但没有符合条件的数据。"
GUARDRAIL_FAILED_ANSWER = "生成的回答未通过数值校验，已拦截。"


@dataclass(frozen=True)
class ChatResponse:
    """JSON-ready contract consumed by the future M5 chat API."""

    question: str
    answer: str
    refused: bool
    refusal_reason: RefusalReason | None
    sql: str | None
    final_sql: str | None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    verdict: GuardrailVerdict | None
    draft_answer: str | None
    usage: TokenUsage

    def to_dict(self) -> dict[str, Any]:
        """Return a structure accepted by ``json.dumps`` without a custom encoder."""
        return asdict(self)


def _add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
    )


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _json_rows(result: SafeResult) -> list[list[Any]]:
    return [[_json_value(value) for value in row] for row in result.rows]


def _response(
    *,
    question: str,
    answer: str,
    usage: TokenUsage,
    refused: bool = False,
    refusal_reason: RefusalReason | None = None,
    sql: str | None = None,
    safe_result: SafeResult | None = None,
    verdict: GuardrailVerdict | None = None,
    draft_answer: str | None = None,
) -> ChatResponse:
    return ChatResponse(
        question=question,
        answer=answer,
        refused=refused,
        refusal_reason=refusal_reason,
        sql=sql,
        final_sql=safe_result.final_sql if safe_result else None,
        columns=list(safe_result.columns) if safe_result else [],
        rows=_json_rows(safe_result) if safe_result else [],
        row_count=safe_result.row_count if safe_result else 0,
        verdict=verdict,
        draft_answer=draft_answer,
        usage=usage,
    )


def _answer_messages(
    question: str, sql: str, result: SafeResult
) -> list[dict[str, str]]:
    rows = _json_rows(result)
    shown_rows = rows[:50]
    truncation = (
        f"\n注：结果共 {result.row_count} 行，下方仅提供前 50 行。"
        if result.row_count > 50
        else ""
    )
    payload = json.dumps(
        {"columns": result.columns, "rows": shown_rows}, ensure_ascii=False
    )
    return [
        {
            "role": "system",
            "content": (
                "你是 ChainPilot 供应链分析助手。请用简短中文回答。"
                "你只能引用下方结果集中已出现的数字和日期；"
                "不许计算新数字，不许推测，不许补充结果外的事实。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{question}\nSQL：{sql}\n结果集：{payload}{truncation}",
        },
    ]


def answer_question(question: str, llm: ChatLLM) -> ChatResponse:
    """Run the complete two-call chat chain and block unsupported numeric claims."""
    generated = generate_sql(question, llm)
    if generated.status == "no_answer":
        return _response(
            question=question,
            answer=OUT_OF_SCOPE_ANSWER,
            refused=True,
            refusal_reason="out_of_scope",
            usage=generated.usage,
        )
    if generated.status != "ok" or generated.sql is None:
        return _response(
            question=question,
            answer=GENERATION_FAILED_ANSWER,
            refused=True,
            refusal_reason="generation_failed",
            usage=generated.usage,
        )

    safe_result = execute_safe(generated.sql)
    if not safe_result.ok:
        return _response(
            question=question,
            answer=f"查询被安全策略拒绝：{safe_result.rejected_reason}",
            refused=True,
            refusal_reason="sql_rejected",
            sql=generated.sql,
            safe_result=safe_result,
            usage=generated.usage,
        )
    if safe_result.row_count == 0:
        return _response(
            question=question,
            answer=EMPTY_RESULT_ANSWER,
            sql=generated.sql,
            safe_result=safe_result,
            usage=generated.usage,
        )

    answer_result = llm.chat(
        _answer_messages(question, generated.sql, safe_result),
        temperature=0.0,
        timeout=30,
    )
    usage = _add_usage(generated.usage, answer_result.usage)
    draft = answer_result.content.strip()
    verdict = verify_answer(draft, safe_result, question)
    if verdict.verdict == "fail":
        return _response(
            question=question,
            answer=GUARDRAIL_FAILED_ANSWER,
            refused=True,
            refusal_reason="guardrail_failed",
            sql=generated.sql,
            safe_result=safe_result,
            verdict=verdict,
            draft_answer=draft,
            usage=usage,
        )
    return _response(
        question=question,
        answer=draft,
        sql=generated.sql,
        safe_result=safe_result,
        verdict=verdict,
        usage=usage,
    )


def _print_rows(response: ChatResponse) -> None:
    if not response.columns:
        print("(no rows)")
        return
    print(" | ".join(response.columns))
    print("-+-".join("-" * len(column) for column in response.columns))
    for row in response.rows:
        print(" | ".join(str(value) for value in row))


def main(argv: Sequence[str] | None = None) -> int:
    """Run one real question and print answer, evidence, SQL, rows, and token cost."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(f'Usage: {sys.executable} api/agent/chat.py "<question>"', file=sys.stderr)
        return 2
    response = answer_question(args[0], DeepSeekClient())
    print(f"回答：{response.answer}")
    print(
        f"Tokens: prompt={response.usage.prompt_tokens}, "
        f"completion={response.usage.completion_tokens}, total={response.usage.total_tokens}"
    )
    if response.verdict:
        print(f"护栏：{response.verdict.verdict}")
        for value, coordinate in response.verdict.matched.items():
            print(f"  matched {value} -> row={coordinate[0]}, column={coordinate[1]}")
        for value in response.verdict.unmatched:
            print(f"  unmatched {value}")
    if response.sql:
        print(f"SQL:\n{response.sql}")
    if response.final_sql and response.final_sql != response.sql:
        print(f"Final SQL:\n{response.final_sql}")
    print(f"结果（{response.row_count} 行）：")
    _print_rows(response)
    return 1 if response.refused and response.refusal_reason != "out_of_scope" else 0


if __name__ == "__main__":
    raise SystemExit(main())
