"""M6-T1: drift-detection unit tests and byte-identity smoke for the summary report."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.summary_report import (  # noqa: E402
    FROZEN_CHAT,
    FROZEN_FORECAST,
    FROZEN_RISK,
    REPORT_PATH,
    build_report,
    drift_errors,
)


def test_drift_errors_passes_within_display_precision() -> None:
    """浮点尾数在封版展示精度内一律视为一致；int 精确相等。"""
    frozen: dict[str, float | int] = {"mape": 47.47, "improvement": 6.9, "rows": 9}
    recomputed: dict[str, float | int] = {"mape": 47.4727, "improvement": 6.9149, "rows": 9}
    assert drift_errors(recomputed, frozen) == []


def test_drift_errors_reports_drift_with_name_and_both_values() -> None:
    frozen: dict[str, float | int] = {"mape": 47.47, "gap": 54978}
    recomputed: dict[str, float | int] = {"mape": 48.11, "gap": 54978}
    errors = drift_errors(recomputed, frozen)
    assert len(errors) == 1
    assert "mape" in errors[0]
    assert "47.47" in errors[0]
    assert "48.11" in errors[0]


def test_drift_errors_int_is_strict_not_rounded() -> None:
    """整数封版值不允许"四舍五入后相等"这种宽容：54979 ≠ 54978。"""
    errors = drift_errors({"gap": 54979}, {"gap": 54978})
    assert len(errors) == 1
    assert "gap" in errors[0]


def test_drift_errors_flags_missing_metric() -> None:
    errors = drift_errors({}, {"mape": 47.47})
    assert errors == ["metric_missing: mape"]


def test_frozen_constants_cover_three_sources() -> None:
    """封版常数三块齐全——防止误删导致漂移检测悄悄失去覆盖。"""
    assert len(FROZEN_FORECAST) == 9
    assert len(FROZEN_RISK) == 8
    assert len(FROZEN_CHAT) == 12


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_generated_report_matches_committed_file_byte_for_byte() -> None:
    """真实库上重算+渲染的内容必须与仓库中已提交的总报告逐字节一致（防文档漂移）。"""
    text, errors = build_report()
    assert errors == []
    assert text == REPORT_PATH.read_text(encoding="utf-8")
