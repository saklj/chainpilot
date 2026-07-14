"""Validate and score the 50-question ChainPilot chat evaluation set."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agent.chat import ChatResponse, answer_question  # noqa: E402
from agent.glossary import SCHEMA_OBJECTS  # noqa: E402
from agent.llm import DeepSeekClient, TokenUsage  # noqa: E402
from agent.safe_sql import database_path  # noqa: E402

QUESTIONS_PATH = Path(__file__).with_name("questions_50.jsonl")
RESULTS_DIR = Path(__file__).with_name("results")

# Official DeepSeek Models & Pricing page checked 2026-07-14. The client records total
# prompt tokens rather than cache-hit/miss splits, so cost uses the conservative cache-miss
# price for deepseek-v4-flash (the default model configured by this project).
PRICING_AS_OF = "2026-07-14"
INPUT_USD_PER_MILLION = Decimal("0.14")
OUTPUT_USD_PER_MILLION = Decimal("0.28")

REQUIRED_FIELDS = {"id", "category", "question", "check", "gold_sql", "source", "note"}
EXPECTED_COUNTS = {"template": 20, "open": 20, "adversarial": 10}
CHECK_TYPES = {"exact_set", "superset", "contains_values", "refusal", "answer_keywords"}
SOURCES = {"designed", "user_testing"}


@dataclass(frozen=True)
class GradeResult:
    passed: bool
    reason: str


def read_questions(path: str | Path = QUESTIONS_PATH) -> tuple[list[dict[str, Any]], list[str]]:
    """Read JSONL while retaining line-specific parse errors for validate mode."""
    questions: list[dict[str, Any]] = []
    errors: list[str] = []
    source = Path(path)
    if not source.is_file():
        return [], [f"file_not_found: {source}"]
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid_json: {exc.msg}")
            continue
        if not isinstance(item, dict):
            errors.append(f"line {line_number}: question must be a JSON object")
            continue
        questions.append(item)
    return questions, errors


def _gold_queries(item: dict[str, Any]) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    gold_sql = item.get("gold_sql")
    if isinstance(gold_sql, str) and gold_sql.strip():
        queries.append(("gold_sql", gold_sql))
    check = item.get("check")
    if isinstance(check, dict):
        also_sql = check.get("also_superset_gold")
        if isinstance(also_sql, str) and also_sql.strip():
            queries.append(("also_superset_gold", also_sql))
    return queries


def _run_gold(
    connection: duckdb.DuckDBPyConnection, sql: str
) -> list[tuple[Any, ...]]:
    statements = connection.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("gold SQL must contain exactly one SELECT statement")
    return connection.execute(sql).fetchall()


def validate_questions(
    questions: list[dict[str, Any]], connection: duckdb.DuckDBPyConnection
) -> list[str]:
    """Validate schema, composition, coverage, and non-empty executable gold SQL."""
    errors: list[str] = []
    ids: list[str] = []
    categories: Counter[str] = Counter()
    query_texts: list[str] = []
    for index, item in enumerate(questions, 1):
        label = str(item.get("id") or f"row_{index}")
        missing = sorted(REQUIRED_FIELDS - set(item))
        if missing:
            errors.append(f"{label}: missing_fields: {', '.join(missing)}")
        question_id = item.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            errors.append(f"{label}: id must be a non-empty string")
        else:
            ids.append(question_id)
        category = item.get("category")
        if category not in EXPECTED_COUNTS:
            errors.append(f"{label}: invalid category: {category}")
        else:
            categories[category] += 1
        if not isinstance(item.get("question"), str) or not item["question"].strip():
            errors.append(f"{label}: question must be a non-empty string")
        if item.get("source") not in SOURCES:
            errors.append(f"{label}: invalid source: {item.get('source')}")
        if not isinstance(item.get("note"), str) or not item["note"].strip():
            errors.append(f"{label}: note must be a non-empty string")

        check = item.get("check")
        if not isinstance(check, dict):
            errors.append(f"{label}: check must be an object")
            continue
        check_type = check.get("type")
        if check_type not in CHECK_TYPES:
            errors.append(f"{label}: invalid check type: {check_type}")
            continue
        if check_type in {"exact_set", "superset", "contains_values"} and not (
            isinstance(item.get("gold_sql"), str) and item["gold_sql"].strip()
        ):
            errors.append(f"{label}: {check_type} requires non-empty gold_sql")
        if check_type == "answer_keywords":
            keywords = check.get("keywords")
            if not isinstance(keywords, list) or not keywords or not all(
                isinstance(value, str) and value for value in keywords
            ):
                errors.append(f"{label}: answer_keywords requires non-empty keywords")
            forbidden = check.get("forbidden", [])
            if not isinstance(forbidden, list) or not all(
                isinstance(value, str) for value in forbidden
            ):
                errors.append(f"{label}: forbidden must be a string list")

        for query_name, sql in _gold_queries(item):
            query_texts.append(sql.lower())
            try:
                rows = _run_gold(connection, sql)
            except Exception as exc:
                errors.append(f"{label}: {query_name} execution_error: {exc}")
                continue
            if not rows:
                errors.append(f"{label}: {query_name} returned no rows")
            if check_type == "contains_values" and query_name == "gold_sql" and len(rows) != 1:
                errors.append(f"{label}: contains_values gold_sql must return exactly one row")

    duplicate_ids = sorted(value for value, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"duplicate_ids: {', '.join(duplicate_ids)}")
    for category, expected in EXPECTED_COUNTS.items():
        actual = categories[category]
        if actual != expected:
            errors.append(f"category_count {category}: expected {expected}, got {actual}")
    combined_queries = "\n".join(query_texts)
    missing_objects = sorted(name for name in SCHEMA_OBJECTS if name not in combined_queries)
    if missing_objects:
        errors.append(f"schema_coverage missing: {', '.join(missing_objects)}")
    return errors


def validate_file(
    path: str | Path = QUESTIONS_PATH,
    *,
    db_path: str | Path | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> list[str]:
    questions, errors = read_questions(path)
    owns_connection = connection is None
    if connection is None:
        try:
            connection = duckdb.connect(str(db_path or database_path()), read_only=True)
        except Exception as exc:
            return errors + [f"database_open_error: {exc}"]
    try:
        errors.extend(validate_questions(questions, connection))
    finally:
        if owns_connection:
            connection.close()
    return errors


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        try:
            parsed = Decimal(str(value))
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None


def _normalized_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def values_match(expected: Any, actual: Any) -> bool:
    """Compare cells with the same 0.5% numeric tolerance as the evidence guardrail."""
    expected_number = _as_decimal(expected)
    actual_number = _as_decimal(actual)
    if expected_number is not None and actual_number is not None:
        tolerance = Decimal("0.005") * abs(expected_number)
        return abs(actual_number - expected_number) <= tolerance
    return _normalized_text(expected) == _normalized_text(actual)


def _unique(values: list[Any]) -> list[Any]:
    unique: list[Any] = []
    for value in values:
        if not any(values_match(value, existing) for existing in unique):
            unique.append(value)
    return unique


def _candidate_columns(response: ChatResponse) -> list[list[Any]]:
    width = max((len(row) for row in response.rows), default=0)
    return [
        [row[column] for row in response.rows if column < len(row)]
        for column in range(width)
    ]


def _column_is_superset(expected: list[Any], actual: list[Any]) -> bool:
    return all(any(values_match(value, candidate) for candidate in actual) for value in expected)


def _grade_set(
    response: ChatResponse, gold_rows: list[tuple[Any, ...]], *, exact: bool
) -> GradeResult:
    """Grade entity-set answers; every gold column is an acceptable answer key.

    同一实体可用 id 或名称等价指代（supplier_id / supplier_name、sku_id /
    product_name），gold_sql 可返回多列，命中任意一列即通过。
    """
    if response.refused:
        return GradeResult(False, f"response refused: {response.refusal_reason}")
    gold_width = max((len(row) for row in gold_rows), default=0)
    for gold_index in range(gold_width):
        expected = _unique(
            [row[gold_index] for row in gold_rows if gold_index < len(row)]
        )
        for index, column in enumerate(_candidate_columns(response)):
            actual = _unique(column)
            contains = _column_is_superset(expected, actual)
            if contains and (not exact or len(actual) == len(expected)):
                return GradeResult(
                    True, f"gold column {gold_index} matched response column {index}"
                )
    relation = "exactly match" if exact else "contain"
    return GradeResult(False, f"no response column {relation}s any gold column values")


def grade_response(
    response: ChatResponse,
    check: dict[str, Any],
    gold_rows: list[tuple[Any, ...]] | None = None,
    also_gold_rows: list[tuple[Any, ...]] | None = None,
) -> GradeResult:
    """Apply one deterministic check using only ``ChatResponse`` values."""
    check_type = check["type"]
    if check_type == "refusal":
        return GradeResult(response.refused, "refused" if response.refused else "not refused")
    if check_type == "exact_set":
        return _grade_set(response, gold_rows or [], exact=True)
    if check_type == "superset":
        return _grade_set(response, gold_rows or [], exact=False)
    if check_type == "contains_values":
        if response.refused:
            return GradeResult(False, f"response refused: {response.refusal_reason}")
        # 问题里已明确给出的实体（如 "PN-00003 的库存"）不要求结果集回显
        expected = [
            value
            for value in (gold_rows or [()])[0]
            if _normalized_text(value) not in response.question
        ]
        actual = [value for row in response.rows for value in row]
        missing = [
            value
            for value in expected
            if not any(values_match(value, candidate) for candidate in actual)
        ]
        return GradeResult(
            not missing,
            "all gold cells found" if not missing else f"missing gold cells: {missing}",
        )
    if check_type == "answer_keywords":
        if response.refused:
            return GradeResult(False, f"response refused: {response.refusal_reason}")
        answer = response.answer.casefold()
        missing_keywords = [
            value for value in check.get("keywords", []) if value.casefold() not in answer
        ]
        forbidden_hits = [
            value for value in check.get("forbidden", []) if value.casefold() in answer
        ]
        if missing_keywords:
            return GradeResult(False, f"missing keywords: {missing_keywords}")
        if forbidden_hits:
            return GradeResult(False, f"forbidden keywords: {forbidden_hits}")
        if also_gold_rows is not None:
            set_grade = _grade_set(response, also_gold_rows, exact=False)
            if not set_grade.passed:
                return GradeResult(False, f"keyword pass; also_superset failed: {set_grade.reason}")
        return GradeResult(True, "answer keywords satisfied")
    return GradeResult(False, f"unsupported check type: {check_type}")


def _token_cost(usage: TokenUsage) -> Decimal:
    return (
        Decimal(usage.prompt_tokens) * INPUT_USD_PER_MILLION
        + Decimal(usage.completion_tokens) * OUTPUT_USD_PER_MILLION
    ) / Decimal(1_000_000)


def _percentage(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0


def _serialize_detail(
    item: dict[str, Any], response: ChatResponse, grade: GradeResult
) -> dict[str, Any]:
    verdict = response.verdict.verdict if response.verdict else None
    unmatched = response.verdict.unmatched if response.verdict else []
    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "passed": grade.passed,
        "reason": grade.reason,
        "answer": response.answer,
        "refused": response.refused,
        "refusal_reason": response.refusal_reason,
        "verdict": verdict,
        "unmatched": unmatched,
        "usage": asdict(response.usage),
        "sql": response.sql,
        "final_sql": response.final_sql,
    }


def run_evaluation(
    questions: list[dict[str, Any]], connection: duckdb.DuckDBPyConnection
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run each selected question exactly once and calculate deterministic metrics."""
    llm = DeepSeekClient()
    details: list[dict[str, Any]] = []
    total_usage = TokenUsage()
    for item in questions:
        try:
            response = answer_question(item["question"], llm)
            gold_rows = (
                _run_gold(connection, item["gold_sql"])
                if isinstance(item.get("gold_sql"), str)
                else None
            )
            also_sql = item["check"].get("also_superset_gold")
            also_rows = _run_gold(connection, also_sql) if also_sql else None
            grade = grade_response(response, item["check"], gold_rows, also_rows)
            details.append(_serialize_detail(item, response, grade))
            total_usage = TokenUsage(
                total_usage.prompt_tokens + response.usage.prompt_tokens,
                total_usage.completion_tokens + response.usage.completion_tokens,
            )
        except Exception as exc:
            details.append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "question": item["question"],
                    "passed": False,
                    "reason": f"evaluation_error: {exc}",
                    "answer": None,
                    "refused": False,
                    "refusal_reason": None,
                    "verdict": None,
                    "unmatched": [],
                    "usage": asdict(TokenUsage()),
                    "sql": None,
                    "final_sql": None,
                }
            )

    category_summary: dict[str, Any] = {}
    for category in EXPECTED_COUNTS:
        selected = [detail for detail in details if detail["category"] == category]
        passed = sum(detail["passed"] for detail in selected)
        category_summary[category] = {
            "passed": passed,
            "total": len(selected),
            "rate_pct": _percentage(passed, len(selected)),
        }
    data_details = [detail for detail in details if detail["category"] != "adversarial"]
    adversarial_details = [
        detail for detail in details if detail["category"] == "adversarial"
    ]
    sql_passed = sum(detail["passed"] for detail in data_details)
    numeric_passed = sum(
        detail["verdict"] == "pass" and not detail["unmatched"]
        for detail in data_details
    )
    adversarial_passed = sum(detail["refused"] for detail in adversarial_details)
    summary = {
        "categories": category_summary,
        "sql_execution_accuracy": {
            "passed": sql_passed,
            "total": len(data_details),
            "rate_pct": _percentage(sql_passed, len(data_details)),
        },
        "answer_numeric_accuracy": {
            "passed": numeric_passed,
            "total": len(data_details),
            "rate_pct": _percentage(numeric_passed, len(data_details)),
        },
        "adversarial_detection_rate": {
            "passed": adversarial_passed,
            "total": len(adversarial_details),
            "rate_pct": _percentage(adversarial_passed, len(adversarial_details)),
        },
        "usage": asdict(total_usage),
        "estimated_cost_usd": float(_token_cost(total_usage)),
    }
    return details, summary


def _print_summary(details: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    print("Category      Passed  Total  Rate")
    for category, values in summary["categories"].items():
        print(
            f"{category:<13} {values['passed']:>6} {values['total']:>6} "
            f"{values['rate_pct']:>6.2f}%"
        )
    for label, key in (
        ("SQL执行正确率", "sql_execution_accuracy"),
        ("答案数字准确率", "answer_numeric_accuracy"),
        ("对抗题检出率", "adversarial_detection_rate"),
    ):
        values = summary[key]
        print(
            f"{label}: {values['passed']}/{values['total']} "
            f"({values['rate_pct']:.2f}%)"
        )
    usage = summary["usage"]
    print(
        f"Tokens: prompt={usage['prompt_tokens']}, completion={usage['completion_tokens']}, "
        f"total={usage['prompt_tokens'] + usage['completion_tokens']}"
    )
    print(
        f"估算成本: ${summary['estimated_cost_usd']:.6f} USD "
        f"(价格查询日 {PRICING_AS_OF}, input cache-miss ${INPUT_USD_PER_MILLION}/M, "
        f"output ${OUTPUT_USD_PER_MILLION}/M)"
    )
    failures = [detail for detail in details if not detail["passed"]]
    if failures:
        print("\n失败题：")
        for detail in failures:
            print(f"- {detail['id']} {detail['question']}: {detail['reason']}")


def _select_questions(
    questions: list[dict[str, Any]],
    *,
    category: str | None,
    ids: list[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    selected = questions
    if category:
        selected = [item for item in selected if item["category"] == category]
    if ids:
        requested = {
            value.strip()
            for group in ids
            for value in group.split(",")
            if value.strip()
        }
        selected = [item for item in selected if item["id"] in requested]
    if limit is not None:
        selected = selected[:limit]
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or run the ChainPilot eval set")
    parser.add_argument("--validate", action="store_true", help="validate without LLM calls")
    parser.add_argument("--category", choices=sorted(EXPECTED_COUNTS))
    parser.add_argument("--ids", nargs="+", help="question ids, space- or comma-separated")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")

    questions, read_errors = read_questions(args.questions)
    connection = duckdb.connect(str(database_path()), read_only=True)
    try:
        errors = read_errors + validate_questions(questions, connection)
        if errors:
            print("评测集校验失败：", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 1
        if args.validate:
            print(f"评测集校验通过：{len(questions)}/{sum(EXPECTED_COUNTS.values())}")
            print("类别计数：template=20, open=20, adversarial=10")
            print(f"schema 覆盖：{len(SCHEMA_OBJECTS)}/{len(SCHEMA_OBJECTS)}")
            return 0

        selected = _select_questions(
            questions, category=args.category, ids=args.ids, limit=args.limit
        )
        if not selected:
            print("过滤后没有题目。", file=sys.stderr)
            return 1
        details, summary = run_evaluation(selected, connection)
    finally:
        connection.close()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RESULTS_DIR / f"eval_{timestamp}.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pricing": {
            "as_of": PRICING_AS_OF,
            "input_cache_miss_usd_per_million": float(INPUT_USD_PER_MILLION),
            "output_usd_per_million": float(OUTPUT_USD_PER_MILLION),
        },
        "filters": {"category": args.category, "ids": args.ids, "limit": args.limit},
        "summary": summary,
        "questions": details,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(details, summary)
    print(f"明细结果：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
