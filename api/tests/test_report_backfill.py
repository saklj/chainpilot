"""Smoke tests for historical weekly-report generation and export."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest
from fastapi import HTTPException

from agent.report import backfill_reports, generate_report
from app.routers.report import report_workbook

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_historical_report_backfill_is_complete_and_idempotent(tmp_path: Path) -> None:
    """Generate and export historical reports using only a disposable DB copy."""
    db_copy = tmp_path / "chainpilot.duckdb"
    shutil.copyfile(REAL_DB, db_copy)
    connection = duckdb.connect(str(db_copy))
    try:
        current_date = date(2016, 5, 22)
        historical_date = date(2016, 5, 15)
        # The real DB may already hold backfilled historical reports; keep only the
        # current one so the counts below stay independent of prior backfills.
        connection.execute(
            "DELETE FROM weekly_report WHERE report_date <> ?", [current_date]
        )
        current_before = connection.execute(
            "SELECT content_md FROM weekly_report WHERE report_date = ?", [current_date]
        ).fetchone()[0]

        historical = generate_report(
            None, connection=connection, report_date=historical_date
        )
        assert "2016-05-15" in historical.content_md
        assert "2016-05-08" in historical.content_md
        assert connection.execute(
            "SELECT count(*) FROM weekly_report WHERE report_date = ?", [historical_date]
        ).fetchone()[0] == 1
        current_after = connection.execute(
            "SELECT content_md FROM weekly_report WHERE report_date = ?", [current_date]
        ).fetchone()[0]
        assert current_after == current_before

        first_backfill = backfill_reports(None, connection=connection)
        assert len(first_backfill) == 7
        first_rows = connection.execute(
            "SELECT report_date, created_at FROM weekly_report ORDER BY report_date"
        ).fetchall()
        assert len(first_rows) == 9

        second_backfill = backfill_reports(None, connection=connection)
        second_rows = connection.execute(
            "SELECT report_date, created_at FROM weekly_report ORDER BY report_date"
        ).fetchall()
        assert second_backfill == []
        assert second_rows == first_rows

        with pytest.raises(ValueError, match="2016-03-27 to 2016-05-22"):
            generate_report(
                None, connection=connection, report_date=date(2016, 1, 1)
            )

        response = report_workbook(historical_date, connection)
        assert response.status_code == 200
        assert response.body
        with pytest.raises(HTTPException) as exc_info:
            report_workbook(date(2016, 1, 1), connection)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["code"] == "report_not_found"
    finally:
        connection.close()
