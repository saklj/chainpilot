"""Thin data-access helpers for material-demand expansion."""

from __future__ import annotations

import duckdb
import pandas as pd

Contributor = dict[str, str | float]


def load_material_demand(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return the LightGBM-expanded daily demand for every forecast material."""
    demand = connection.execute(
        "SELECT material_pn, date, CAST(demand_qty AS DOUBLE) AS demand_qty "
        "FROM v_material_demand_daily ORDER BY material_pn, date"
    ).fetchdf()
    demand["date"] = pd.to_datetime(demand["date"]).astype("datetime64[ns]")
    demand["demand_qty"] = demand["demand_qty"].astype(float)
    return demand


def top_sku_contributors(
    connection: duckdb.DuckDBPyConnection, material_pn: str, k: int = 3
) -> list[Contributor]:
    """Return the top-k SKU contributors to one material's future demand."""
    if k <= 0:
        raise ValueError("k must be positive")
    rows = connection.execute(
        "SELECT f.sku_id, CAST(SUM(f.yhat * b.qty_per_unit) AS DOUBLE) AS demand_qty "
        "FROM forecast_daily f JOIN bom b USING (sku_id) "
        "WHERE f.model_name = 'lightgbm' AND b.material_pn = ? "
        "GROUP BY f.sku_id ORDER BY demand_qty DESC, f.sku_id LIMIT ?",
        [material_pn, k],
    ).fetchall()
    return [{"sku_id": str(sku_id), "demand_qty": float(quantity)} for sku_id, quantity in rows]
