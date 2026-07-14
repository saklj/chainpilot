"""Deterministic evidence verification for numbers and dates in generated answers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

if __package__:
    from .safe_sql import SafeResult
else:
    from safe_sql import SafeResult

VerdictStatus = Literal["pass", "fail"]
CellCoordinate = tuple[int, int]


@dataclass(frozen=True)
class GuardrailVerdict:
    """Evidence matches use zero-based ``(row_index, column_index)`` coordinates."""

    verdict: VerdictStatus
    matched: dict[str, CellCoordinate]
    unmatched: list[str]
    checked_count: int


@dataclass(frozen=True)
class _Candidate:
    kind: Literal["number", "percentage", "date"]
    display: str
    span: tuple[int, int]
    numeric_values: tuple[Decimal, ...] = ()
    date_value: date | None = None


# IDs are categorical evidence rather than quantities. Mask the complete token first so
# PN-00003, SUP-013, SKU-001 and FOODS_3_090 do not create numeric hallucination candidates.
IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][A-Z0-9]*)(?:[-_][A-Z0-9]+)+(?![A-Za-z0-9])"
)
ISO_DATE_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d{4})-(\d{1,2})-(\d{1,2})(?![A-Za-z0-9])"
)
CHINESE_DATE_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<number>(?:±|[+\-−])?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<percent>[%％])?"
    r"(?![A-Za-z0-9_])"
)


def _mask_identifiers(text: str) -> str:
    return IDENTIFIER_RE.sub(lambda match: " " * len(match.group(0)), text)


def _mask_result_strings(text: str, literals: list[str]) -> str:
    """Mask exact result text while preserving offsets used by candidate coordinates."""
    masked = text
    # Longer values must win when one cell is a substring of another (for example,
    # "Carton" and "Carton 271"). Exact, case-sensitive replacement prevents a bare
    # number such as "271" from inheriting evidence from the descriptive cell text.
    for literal in sorted(set(literals), key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(literal)}(?![A-Za-z0-9])"
        )
        masked = pattern.sub(" " * len(literal), masked)
    return masked


def _identifier_numbers(text: str) -> set[Decimal]:
    """Return numeric components of IDs when those IDs occur in the user question."""
    return {
        Decimal(digits)
        for identifier in IDENTIFIER_RE.finditer(text)
        for digits in re.findall(r"\d+", identifier.group(0))
    }


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f").rstrip("0").rstrip(".")


def _parse_decimal(raw: str) -> Decimal | None:
    cleaned = raw.replace(",", "").replace("，", "").replace("−", "-")
    if cleaned.startswith("±"):
        cleaned = cleaned[1:]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in occupied)


def _extract_candidates(
    text: str, *, result_string_literals: list[str] | None = None
) -> list[_Candidate]:
    masked = _mask_result_strings(text, result_string_literals or [])
    masked = _mask_identifiers(masked)
    candidates: list[_Candidate] = []
    date_spans: list[tuple[int, int]] = []
    for pattern in (ISO_DATE_RE, CHINESE_DATE_RE):
        for match in pattern.finditer(masked):
            span = match.span()
            if _overlaps(span, date_spans):
                continue
            try:
                value = date(*(int(part) for part in match.groups()))
            except ValueError:
                continue
            date_spans.append(span)
            candidates.append(
                _Candidate("date", value.isoformat(), span, date_value=value)
            )

    for match in NUMBER_RE.finditer(masked):
        if _overlaps(match.span(), date_spans):
            continue
        value = _parse_decimal(match.group("number"))
        if value is None:
            continue
        if match.group("percent"):
            display = f"{_decimal_text(value)}%"
            candidates.append(
                _Candidate(
                    "percentage",
                    display,
                    match.span(),
                    numeric_values=(value, value / Decimal(100)),
                )
            )
        else:
            candidates.append(
                _Candidate(
                    "number",
                    _decimal_text(value),
                    match.span(),
                    numeric_values=(value,),
                )
            )
    return sorted(candidates, key=lambda candidate: candidate.span)


def _cell_number(value: Any) -> Decimal | None:
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
    if isinstance(value, str):
        match = NUMBER_RE.fullmatch(value.strip())
        if match and not match.group("percent"):
            return _parse_decimal(match.group("number"))
    return None


def _cell_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        for pattern in (ISO_DATE_RE, CHINESE_DATE_RE):
            match = pattern.fullmatch(stripped)
            if match:
                try:
                    return date(*(int(part) for part in match.groups()))
                except ValueError:
                    return None
    return None


def _numeric_match(answer_value: Decimal, cell_value: Decimal) -> bool:
    tolerance = Decimal("0.005") * abs(cell_value)
    return abs(answer_value - cell_value) <= tolerance


def _is_ordinal(text: str, candidate: _Candidate, row_count: int) -> bool:
    if candidate.kind != "number" or len(candidate.numeric_values) != 1:
        return False
    value = candidate.numeric_values[0]
    if value != value.to_integral() or not 1 <= value <= row_count:
        return False
    start, end = candidate.span
    before = text[max(0, start - 1) : start]
    after = text[end : end + 1]
    return before == "第" or after in {".", "．", "、"}


def verify_answer(answer: str, safe_result: SafeResult, question: str) -> GuardrailVerdict:
    """Verify every non-exempt answer number or date against a result-set cell."""
    # Exact descriptive strings copied from result cells are categorical evidence, not new
    # numeric claims. Only mask non-numeric strings of length >=2: a cell containing bare
    # "271" must remain a numeric candidate and pass through the ordinary evidence check.
    result_string_literals = [
        stripped
        for row in safe_result.rows
        for cell in row
        if isinstance(cell, str)
        if len(stripped := cell.strip()) >= 2 and _cell_number(stripped) is None
    ]
    answer_candidates = _extract_candidates(
        answer, result_string_literals=result_string_literals
    )
    question_candidates = _extract_candidates(question)
    question_numbers = {
        value
        for candidate in question_candidates
        for value in candidate.numeric_values
    } | _identifier_numbers(question)
    question_dates = {
        candidate.date_value
        for candidate in question_candidates
        if candidate.date_value is not None
    }

    numeric_cells: list[tuple[Decimal, CellCoordinate]] = []
    date_cells: list[tuple[date, CellCoordinate]] = []
    for row_index, row in enumerate(safe_result.rows):
        for column_index, cell in enumerate(row):
            coordinate = (row_index, column_index)
            numeric = _cell_number(cell)
            if numeric is not None:
                numeric_cells.append((numeric, coordinate))
            cell_date = _cell_date(cell)
            if cell_date is not None:
                date_cells.append((cell_date, coordinate))
    result_years = {value.year for value, _ in date_cells}

    matched: dict[str, CellCoordinate] = {}
    unmatched: list[str] = []
    checked_count = 0
    for candidate in answer_candidates:
        # Numbers already present in the user's question are instructions/identifiers, not
        # claims newly introduced by the answer, so they are outside evidence verification.
        if candidate.kind == "date" and candidate.date_value in question_dates:
            continue
        if candidate.numeric_values and any(
            value in question_numbers for value in candidate.numeric_values
        ):
            continue

        # Models often state "N rows" or number result rows as 1., 2., ...; those values
        # describe presentation shape rather than a database cell.
        if candidate.numeric_values == (Decimal(safe_result.row_count),):
            continue
        if _is_ordinal(answer, candidate, safe_result.row_count):
            continue

        # A standalone year is permitted when a returned date supplies that exact year; the
        # full date remains subject to exact date matching below.
        if (
            candidate.kind == "number"
            and len(candidate.numeric_values) == 1
            and candidate.numeric_values[0] == candidate.numeric_values[0].to_integral()
            and int(candidate.numeric_values[0]) in result_years
        ):
            continue

        checked_count += 1
        coordinate: CellCoordinate | None = None
        if candidate.kind == "date" and candidate.date_value is not None:
            coordinate = next(
                (
                    cell_coordinate
                    for value, cell_coordinate in date_cells
                    if value == candidate.date_value
                ),
                None,
            )
        elif candidate.numeric_values:
            coordinate = next(
                (
                    cell_coordinate
                    for cell_value, cell_coordinate in numeric_cells
                    if any(
                        _numeric_match(answer_value, cell_value)
                        for answer_value in candidate.numeric_values
                    )
                ),
                None,
            )
        if coordinate is None:
            unmatched.append(candidate.display)
        else:
            matched.setdefault(candidate.display, coordinate)

    return GuardrailVerdict(
        verdict="fail" if unmatched else "pass",
        matched=matched,
        unmatched=unmatched,
        checked_count=checked_count,
    )
