"""Smoke tests for leakage-safe historical material-risk backfill."""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from agent.report import assemble_report_data
from analytics.backfill import backfill_risk

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"


def _risk_snapshot(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return connection.execute(
        "SELECT material_pn, eval_date, doi_days, lt_coverage, supplier_concentration, "
        "gap_qty, gap_date, risk_level, risk_reasons FROM material_risk "
        "ORDER BY eval_date, material_pn"
    ).fetchdf()


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_backfill_smoke_is_complete_idempotent_and_enables_comparison(
    tmp_path: Path,
) -> None:
    """Backfill one period on a disposable DB copy and preserve the current snapshot."""
    db_copy = tmp_path / "chainpilot.duckdb"
    shutil.copyfile(REAL_DB, db_copy)
    connection = duckdb.connect(str(db_copy))
    try:
        anchor = connection.execute("SELECT MAX(date) FROM sales_daily").fetchone()[0]
        # The real DB may already hold backfilled history; clear the one cutoff this
        # test regenerates so its assertions stay independent of prior backfills.
        cutoff = anchor - timedelta(days=7)
        connection.execute("DELETE FROM material_risk WHERE eval_date = ?", [cutoff])
        dates_before = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT eval_date FROM material_risk"
            ).fetchall()
        }
        anchor_before = connection.execute(
            "SELECT * FROM material_risk WHERE eval_date = ? ORDER BY material_pn",
            [anchor],
        ).fetchdf()
        anchor_count = len(anchor_before)
        assert anchor_count == 300

        summary = backfill_risk(connection, periods=1)
        assert len(summary) == 1

        dates_after = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT eval_date FROM material_risk"
            ).fetchall()
        }
        new_dates = dates_after - dates_before
        assert len(new_dates) == 1
        historical_date = new_dates.pop()
        historical_count = connection.execute(
            "SELECT COUNT(*) FROM material_risk WHERE eval_date = ?", [historical_date]
        ).fetchone()[0]
        assert historical_count == anchor_count

        anchor_after = connection.execute(
            "SELECT * FROM material_risk WHERE eval_date = ? ORDER BY material_pn",
            [anchor],
        ).fetchdf()
        pd.testing.assert_frame_equal(anchor_after, anchor_before)

        null_count = connection.execute(
            "SELECT COUNT(*) FROM material_risk WHERE eval_date = ? AND ("
            "doi_days IS NULL OR lt_coverage IS NULL OR supplier_concentration IS NULL OR "
            "gap_qty IS NULL OR risk_level IS NULL OR risk_reasons IS NULL)",
            [historical_date],
        ).fetchone()[0]
        assert null_count == 0

        # open_po lacks an order date, so historical supply uses eta_date > cutoff as the
        # documented approximation and may include orders not yet placed at that cutoff.
        first_backfill = _risk_snapshot(connection)
        backfill_risk(connection, periods=1)
        second_backfill = _risk_snapshot(connection)
        pd.testing.assert_frame_equal(second_backfill, first_backfill)

        report_data = assemble_report_data(connection)
        assert report_data.comparison.previous_date is not None
        assert isinstance(report_data.comparison.red_change, int)
        assert isinstance(report_data.comparison.orange_change, int)
    finally:
        connection.close()
