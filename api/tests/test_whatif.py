"""Read-only smoke tests for supplier-outage simulation."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from analytics.whatif import simulate_supplier_outage
from app.main import app
from app.schemas import WhatIfResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_supplier_outage_is_read_only_monotonic_and_deterministic() -> None:
    connection = duckdb.connect(str(REAL_DB), read_only=True)
    try:
        supplier_id = connection.execute(
            "SELECT supplier_id FROM v_risk_by_supplier "
            "WHERE eval_date = (SELECT max(eval_date) FROM material_risk) "
            "ORDER BY weighted_gap_qty DESC, supplier_id LIMIT 1"
        ).fetchone()[0]
        result = simulate_supplier_outage(connection, supplier_id, 14)
        repeated = simulate_supplier_outage(connection, supplier_id, 14)
        assert result.worsened_materials
        assert isinstance(result.summary.new_red_count, int)
        assert isinstance(result.summary.new_orange_count, int)
        assert isinstance(result.summary.total_gap_delta, int)
        assert isinstance(result.summary.affected_sku_count, int)
        assert isinstance(result.summary.exposure_amount, float)
        assert (result.scenario_risks["gap_qty"] >= result.baseline_risks["gap_qty"]).all()
        assert result.summary.baseline_red_count == 20
        assert result.summary.baseline_orange_count == 16
        assert result.summary == repeated.summary
        assert result.worsened_materials == repeated.worsened_materials
        assert result.affected_skus == repeated.affected_skus
        pd.testing.assert_frame_equal(result.baseline_risks, repeated.baseline_risks)
        pd.testing.assert_frame_equal(result.scenario_risks, repeated.scenario_risks)
        with pytest.raises(ValueError, match="not found"):
            simulate_supplier_outage(connection, "SUP-NOT-FOUND", 14)
    finally:
        connection.close()


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_whatif_routes_validate_and_serialize() -> None:
    with TestClient(app) as client:
        suppliers = client.get("/api/whatif/suppliers")
        assert suppliers.status_code == 200
        supplier_rows = suppliers.json()
        assert supplier_rows
        assert "weighted_gap_qty" in supplier_rows[0]
        response = client.post(
            "/api/whatif/simulate",
            json={"supplier_id": supplier_rows[0]["supplier_id"], "days": 14},
        )
        assert response.status_code == 200
        WhatIfResponse.model_validate(response.json())

        missing = client.post(
            "/api/whatif/simulate",
            json={"supplier_id": "SUP-NOT-FOUND", "days": 14},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "supplier_not_found"
        assert client.post(
            "/api/whatif/simulate", json={"supplier_id": supplier_rows[0]["supplier_id"], "days": 0}
        ).status_code == 422
        assert client.post(
            "/api/whatif/simulate", json={"supplier_id": supplier_rows[0]["supplier_id"], "days": 29}
        ).status_code == 422
