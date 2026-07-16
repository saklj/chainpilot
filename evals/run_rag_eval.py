"""Evaluate leave-one-out few-shot retrieval and optional fixed-vs-RAG NL2SQL."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agent.chat import ChatResponse  # noqa: E402
from agent.llm import DeepSeekClient, TokenUsage  # noqa: E402
from agent.nl2sql import FEW_SHOTS, FewShot, generate_sql  # noqa: E402
from agent.retrieval import BgeEmbedder, retrieve_examples  # noqa: E402
from agent.safe_sql import database_path, execute_safe  # noqa: E402
from evals.run_eval import _run_gold, grade_response, read_questions  # noqa: E402

RESULT_PATH = REPO_ROOT / "evals" / "results" / "rag_eval.json"
INPUT_USD_PER_MILLION = Decimal("0.14")
OUTPUT_USD_PER_MILLION = Decimal("0.28")


def _run_arm(
    question: dict[str, Any],
    connection: duckdb.DuckDBPyConnection,
    llm: DeepSeekClient,
    few_shots: list[FewShot] | tuple[FewShot, ...],
) -> dict[str, Any]:
    """Grade one arm with M4's per-question check semantics.

    The experiment isolates the NL2SQL layer (few-shot selection is its only variable),
    so the executed result set is wrapped as a minimal ChatResponse and graded by the
    same ``grade_response`` used for the M4 numbers — a stricter row-for-row comparison
    here once mis-scored the fixed arm at 32% versus M4's documented 85%+.
    """
    generated = generate_sql(question["question"], llm, few_shots)
    rows: list[list[Any]] = []
    if generated.status == "ok" and generated.sql:
        result = execute_safe(generated.sql, connection=connection)
        if result.ok:
            rows = [list(row) for row in result.rows]
    response = ChatResponse(
        question=question["question"],
        answer="",
        # Execution failure is NOT a refusal: it must fail value checks, not pass the
        # adversarial refusal check.
        refused=generated.status != "ok",
        refusal_reason=None,
        sql=generated.sql,
        final_sql=generated.sql,
        columns=[],
        rows=rows,
        row_count=len(rows),
        verdict=None,
        draft_answer=None,
        usage=generated.usage,
    )
    gold_rows = (
        _run_gold(connection, question["gold_sql"])
        if isinstance(question.get("gold_sql"), str)
        else None
    )
    also_sql = question["check"].get("also_superset_gold")
    also_rows = _run_gold(connection, also_sql) if also_sql else None
    grade = grade_response(response, question["check"], gold_rows, also_rows)
    return {
        "passed": grade.passed,
        "status": generated.status,
        "sql": generated.sql,
        "usage": asdict(generated.usage),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--min-similarity", type=float)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--db-path", type=Path, default=database_path())
    args = parser.parse_args()
    if args.k <= 0:
        parser.error("--k must be positive")
    questions, errors = read_questions()
    if errors:
        raise SystemExit("questions file is invalid: " + "; ".join(errors))
    connection = duckdb.connect(str(args.db_path), read_only=True)
    embedder = BgeEmbedder()
    retrieval_rows: list[dict[str, Any]] = []
    fixed_usage = TokenUsage()
    rag_usage = TokenUsage()
    comparisons: list[dict[str, Any]] = []
    llm = DeepSeekClient() if args.with_llm else None
    try:
        for item in questions:
            retrieved = retrieve_examples(
                connection,
                embedder,
                item["question"],
                args.k,
                exclude_id=item["id"],
                min_similarity=args.min_similarity,
            )
            categories = [example.category for example in retrieved]
            similarities = [example.similarity for example in retrieved]
            retrieval_rows.append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "retrieved_ids": [example.question_id for example in retrieved],
                    "top1_same_category": bool(categories and categories[0] == item["category"]),
                    "topk_same_category": item["category"] in categories,
                    "similarities": similarities,
                }
            )
            if llm:
                fixed = _run_arm(item, connection, llm, FEW_SHOTS)
                # Hybrid injection: retrieved Top-k plus the pinned refusal example.
                # Refusal is a safety invariant and must not depend on the similarity
                # lottery — v2 lost adversarial A10 exactly because its neighbours were
                # all answerable gap questions (D6 philosophy: guardrails are structural).
                rag_shots = [example.to_few_shot() for example in retrieved] + [
                    shot for shot in FEW_SHOTS if shot.answer == "NO_ANSWER"
                ]
                rag = _run_arm(item, connection, llm, rag_shots)
                fixed_usage = TokenUsage(
                    fixed_usage.prompt_tokens + fixed["usage"]["prompt_tokens"],
                    fixed_usage.completion_tokens + fixed["usage"]["completion_tokens"],
                )
                rag_usage = TokenUsage(
                    rag_usage.prompt_tokens + rag["usage"]["prompt_tokens"],
                    rag_usage.completion_tokens + rag["usage"]["completion_tokens"],
                )
                comparisons.append({"id": item["id"], "fixed": fixed, "rag": rag})
    finally:
        connection.close()

    all_similarities = [value for row in retrieval_rows for value in row["similarities"]]
    summary = {
        "topk_same_category_rate": sum(row["topk_same_category"] for row in retrieval_rows) / len(retrieval_rows),
        "top1_same_category_rate": sum(row["top1_same_category"] for row in retrieval_rows) / len(retrieval_rows),
        "similarity_mean": statistics.fmean(all_similarities) if all_similarities else None,
        "similarity_min": min(all_similarities) if all_similarities else None,
        "fixed_accuracy": (
            sum(row["fixed"]["passed"] for row in comparisons) / len(comparisons)
            if comparisons
            else None
        ),
        "rag_accuracy": (
            sum(row["rag"]["passed"] for row in comparisons) / len(comparisons)
            if comparisons
            else None
        ),
        "fixed_usage": asdict(fixed_usage),
        "rag_usage": asdict(rag_usage),
        "estimated_cost_usd": float(
            (Decimal(fixed_usage.prompt_tokens + rag_usage.prompt_tokens) * INPUT_USD_PER_MILLION
             + Decimal(fixed_usage.completion_tokens + rag_usage.completion_tokens) * OUTPUT_USD_PER_MILLION)
            / Decimal(1_000_000)
        ),
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {"k": args.k, "min_similarity": args.min_similarity, "with_llm": args.with_llm},
        "retrieval": retrieval_rows,
        "comparisons": comparisons,
        "summary": summary,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if comparisons:
        improved = [row["id"] for row in comparisons if row["rag"]["passed"] and not row["fixed"]["passed"]]
        regressed = [row["id"] for row in comparisons if row["fixed"]["passed"] and not row["rag"]["passed"]]
        print(f"RAG改善: {improved or '无'}\nRAG退化: {regressed or '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
