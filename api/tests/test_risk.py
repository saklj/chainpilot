"""Unit and integration tests for the MRP-lite material-risk engine."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics.mrp import load_material_demand, top_sku_contributors
from analytics.risk import (
    NO_DEMAND_DOI,
    calculate_material_risk,
    classify_risk,
    explain_risk,
    projected_shortage,
    run_risk_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"


@pytest.fixture
def mrp_connection() -> duckdb.DuckDBPyConnection:
    """Build the MRP access layer's required tables in memory."""
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE forecast_daily "
        "(sku_id VARCHAR, date DATE, model_name VARCHAR, yhat DOUBLE)"
    )
    connection.execute("CREATE TABLE bom (sku_id VARCHAR, material_pn VARCHAR, qty_per_unit DOUBLE)")
    connection.execute(
        "INSERT INTO forecast_daily VALUES "
        "('SKU-A', '2024-01-02', 'lightgbm', 10), "
        "('SKU-B', '2024-01-02', 'lightgbm', 4), "
        "('SKU-A', '2024-01-02', 'ets', 999)"
    )
    connection.execute("INSERT INTO bom VALUES ('SKU-A', 'PN-1', 2), ('SKU-B', 'PN-1', 1)")
    connection.execute(
        "CREATE VIEW v_material_demand_daily AS "
        "SELECT b.material_pn, f.date, SUM(f.yhat * b.qty_per_unit) AS demand_qty "
        "FROM forecast_daily f JOIN bom b USING (sku_id) "
        "WHERE f.model_name = 'lightgbm' GROUP BY 1, 2"
    )
    yield connection
    connection.close()


def test_mrp_access_helpers(mrp_connection: duckdb.DuckDBPyConnection) -> None:
    """Demand expansion and top contributors use only LightGBM forecasts."""
    demand = load_material_demand(mrp_connection)
    assert demand.iloc[0].to_dict() == {
        "material_pn": "PN-1",
        "date": pd.Timestamp("2024-01-02"),
        "demand_qty": 24.0,
    }
    assert top_sku_contributors(mrp_connection, "PN-1") == [
        {"sku_id": "SKU-A", "demand_qty": 20.0},
        {"sku_id": "SKU-B", "demand_qty": 4.0},
    ]


def test_projected_balance_applies_same_day_receipt_before_demand() -> None:
    """A same-day receipt exactly prevents a gap, which starts on the following day."""
    eval_date = pd.Timestamp("2024-01-01")
    demand = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-02", periods=3),
            "demand_qty": [10.0, 10.0, 10.0],
        }
    )
    orders = pd.DataFrame(
        {"eta_date": [pd.Timestamp("2024-01-03")], "qty": [5]}
    )
    gap_date, gap_qty = projected_shortage(demand, orders, 15, eval_date, horizon=3)
    assert gap_date == pd.Timestamp("2024-01-04")
    assert gap_qty == 10


@pytest.mark.parametrize(
    ("kwargs", "expected_level", "expected_reasons"),
    [
        (
            {"gap_date": pd.Timestamp("2024-01-03"), "gap_qty": 10, "doi_days": 20,
             "min_lt": 3, "supplier_count": 2, "supplier_concentration": 60},
            "RED",
            "GAP_BEFORE_LT",
        ),
        (
            {"gap_date": pd.Timestamp("2024-01-06"), "gap_qty": 10, "doi_days": 20,
             "min_lt": 3, "supplier_count": 2, "supplier_concentration": 60},
            "ORANGE",
            "GAP_IN_HORIZON",
        ),
        (
            {"gap_date": None, "gap_qty": 0, "doi_days": 4, "min_lt": 10,
             "supplier_count": 1, "supplier_concentration": 100},
            "ORANGE",
            "LOW_DOI_SINGLE_SOURCE;LOW_DOI;HIGH_CONCENTRATION",
        ),
        (
            {"gap_date": None, "gap_qty": 0, "doi_days": 9, "min_lt": 10,
             "supplier_count": 2, "supplier_concentration": 50},
            "YELLOW",
            "LOW_DOI",
        ),
        (
            {"gap_date": None, "gap_qty": 0, "doi_days": 12, "min_lt": 10,
             "supplier_count": 2, "supplier_concentration": 80},
            "YELLOW",
            "HIGH_CONCENTRATION",
        ),
        (
            {"gap_date": None, "gap_qty": 0, "doi_days": 20, "min_lt": 10,
             "supplier_count": 2, "supplier_concentration": 60},
            "GREEN",
            "",
        ),
    ],
)
def test_classification_boundaries(
    kwargs: dict[str, object], expected_level: str, expected_reasons: str
) -> None:
    """Every high-to-low classification path returns all exact matching codes."""
    level, reasons = classify_risk(eval_date=pd.Timestamp("2024-01-01"), **kwargs)
    assert level == expected_level
    assert reasons == expected_reasons


def test_zero_demand_uses_green_sentinel() -> None:
    """A material with no forecast demand receives the documented finite sentinel."""
    eval_date = pd.Timestamp("2024-01-01")
    demand = pd.DataFrame(
        {
            "material_pn": ["PN-Z"] * 28,
            "date": pd.date_range("2024-01-02", periods=28),
            "demand_qty": [0.0] * 28,
        }
    )
    inventory = pd.DataFrame(
        {"material_pn": ["PN-Z"], "snapshot_date": [eval_date], "qty_onhand": [10]}
    )
    supply = pd.DataFrame(
        {
            "material_pn": ["PN-Z"],
            "min_lt": [10],
            "supplier_concentration": [50.0],
            "supplier_count": [2],
            "primary_supplier_id": ["SUP-1"],
        }
    )
    orders = pd.DataFrame(columns=["material_pn", "eta_date", "qty"])
    row = calculate_material_risk(demand, inventory, orders, supply, eval_date).iloc[0]
    assert row["doi_days"] == NO_DEMAND_DOI
    assert row["risk_level"] == "GREEN"
    assert row["risk_reasons"] == ""


def test_explain_red_contains_computed_date_and_shortage() -> None:
    """The RED explanation contains its calculated date and formatted gap quantity."""
    row = {
        "material_pn": "PN-X",
        "doi_days": 2.1,
        "min_lt": 78,
        "supplier_concentration": 100.0,
        "gap_date": pd.Timestamp("2024-01-03"),
        "gap_qty": 1240,
        "risk_reasons": "GAP_BEFORE_LT;LOW_DOI_SINGLE_SOURCE;LOW_DOI;HIGH_CONCENTRATION",
        "primary_supplier_id": "SUP-013",
    }
    explanation = explain_risk(row, [{"sku_id": "SKU-A", "demand_qty": 100.0}])
    assert "2024-01-03" in explanation
    assert "1,240" in explanation
    assert "SUP-013" in explanation
    assert "SKU-A" in explanation


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_real_engine_recalls_all_ground_truth_and_builds_views() -> None:
    """The real engine recalls every GT scenario and persists complete deterministic rows."""
    connection = duckdb.connect(str(REAL_DB))
    try:
        risks = run_risk_engine(connection)
        truth = json.loads((REPO_ROOT / "data" / "ground_truth_scenarios.json").read_text())
        indexed = risks.set_index("material_pn")
        for scenario in truth:
            row = indexed.loc[scenario["material_pn"]]
            assert row["risk_level"] in {"RED", "ORANGE"}, scenario
            assert row["gap_qty"] > 0, scenario

        expected_count = connection.execute(
            "SELECT COUNT(DISTINCT material_pn) FROM v_material_demand_daily"
        ).fetchone()[0]
        persisted_count = connection.execute(
            "SELECT COUNT(*) FROM material_risk WHERE eval_date = (SELECT MAX(date) FROM sales_daily)"
        ).fetchone()[0]
        assert persisted_count == expected_count
        null_count = connection.execute(
            "SELECT COUNT(*) FROM material_risk WHERE "
            "doi_days IS NULL OR lt_coverage IS NULL OR supplier_concentration IS NULL OR "
            "gap_qty IS NULL OR risk_level IS NULL OR risk_reasons IS NULL"
        ).fetchone()[0]
        assert null_count == 0
        risky_without_reason = connection.execute(
            "SELECT COUNT(*) FROM material_risk WHERE risk_level IN ('RED', 'ORANGE') "
            "AND risk_reasons = ''"
        ).fetchone()[0]
        assert risky_without_reason == 0

        direct = connection.execute(
            "SELECT risk_level, COUNT(*) FROM material_risk "
            "WHERE eval_date = (SELECT MAX(eval_date) FROM material_risk) GROUP BY risk_level"
        ).fetchall()
        direct_counts = dict(direct)
        for view in ("v_risk_by_commodity", "v_risk_by_item_group"):
            totals = connection.execute(
                f"SELECT SUM(red_count), SUM(orange_count), SUM(yellow_count), "
                f"SUM(green_count) FROM {view} "
                "WHERE eval_date = (SELECT MAX(eval_date) FROM material_risk)"
            ).fetchone()
            assert totals == tuple(direct_counts.get(level, 0) for level in ("RED", "ORANGE", "YELLOW", "GREEN"))
        assert connection.execute("SELECT COUNT(*) FROM v_risk_by_supplier").fetchone()[0] > 0
    finally:
        connection.close()
