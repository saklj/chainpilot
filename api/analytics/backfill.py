"""Leakage-safe historical material-risk backfill.

Each historical cutoff retrains the demand forecast using sales observed on or before
that cutoff, keeps the resulting 28-day forecast in memory, and writes only the matching
``material_risk`` snapshot.

Known limitation: ``open_po`` has no order-date column, so the backfill cannot determine
whether a purchase order existed at a historical cutoff. It therefore reuses the current
engine's ``eta_date > cutoff`` approximation. This can include orders that had not yet
been placed and systematically understate historical shortages.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import duckdb
import pandas as pd

from .forecast import HORIZON, lightgbm_forecast, load_source_frames
from .risk import (
    calculate_material_risk,
    database_path,
    evaluation_date,
    load_future_purchase_orders,
    load_latest_inventory,
    load_supply_profile,
    write_material_risk,
)

SUMMARY_COLUMNS = ("eval_date", "red", "orange", "yellow", "green", "total_gap")


def _load_bom(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load the BOM inputs needed for the in-memory material-demand expansion."""
    bom = connection.execute(
        "SELECT sku_id, material_pn, CAST(qty_per_unit AS DOUBLE) AS qty_per_unit "
        "FROM bom ORDER BY sku_id, material_pn"
    ).fetchdf()
    bom["qty_per_unit"] = bom["qty_per_unit"].astype(float)
    return bom


def _expand_material_demand(forecast: pd.DataFrame, bom: pd.DataFrame) -> pd.DataFrame:
    """Apply the same ``yhat * qty_per_unit`` formula as v_material_demand_daily."""
    expanded = forecast.merge(bom, on="sku_id", how="inner")
    expanded["demand_qty"] = expanded["yhat"] * expanded["qty_per_unit"]
    demand = (
        expanded.groupby(["material_pn", "date"], as_index=False, observed=True)[
            "demand_qty"
        ]
        .sum()
        .sort_values(["material_pn", "date"])
        .reset_index(drop=True)
    )
    demand["date"] = pd.to_datetime(demand["date"]).astype("datetime64[ns]")
    demand["demand_qty"] = demand["demand_qty"].astype(float)
    return demand


def _summary_row(risks: pd.DataFrame, eval_date: pd.Timestamp) -> dict[str, object]:
    counts = risks["risk_level"].value_counts()
    return {
        "eval_date": eval_date,
        "red": int(counts.get("RED", 0)),
        "orange": int(counts.get("ORANGE", 0)),
        "yellow": int(counts.get("YELLOW", 0)),
        "green": int(counts.get("GREEN", 0)),
        "total_gap": int(risks["gap_qty"].sum()),
    }


def backfill_risk(
    connection: duckdb.DuckDBPyConnection, periods: int = 8, step_days: int = 7
) -> pd.DataFrame:
    """Backfill historical risk snapshots in ascending cutoff order.

    The current sales anchor is deliberately excluded. Each cutoff is committed in its
    own transaction, so a failed write cannot leave that period partially persisted.
    """
    if periods <= 0:
        raise ValueError("periods must be positive")
    if step_days <= 0:
        raise ValueError("step_days must be positive")

    anchor = evaluation_date(connection)
    cutoffs = [
        anchor - pd.Timedelta(days=step_days * offset)
        for offset in range(periods, 0, -1)
    ]
    sales, calendar, prices = load_source_frames(connection)
    bom = _load_bom(connection)
    supply_profile = load_supply_profile(connection)
    summaries: list[dict[str, object]] = []

    for cutoff in cutoffs:
        train = sales.loc[sales["date"] <= cutoff].copy()
        forecast_dates = pd.date_range(cutoff + pd.Timedelta(days=1), periods=HORIZON)
        forecast = lightgbm_forecast(train, forecast_dates, calendar, prices)
        demand = _expand_material_demand(forecast, bom)
        inventory = load_latest_inventory(connection, cutoff)
        purchase_orders = load_future_purchase_orders(connection, cutoff)
        risks = calculate_material_risk(
            demand, inventory, purchase_orders, supply_profile, cutoff
        )

        connection.execute("BEGIN TRANSACTION")
        try:
            write_material_risk(connection, risks, cutoff)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        summaries.append(_summary_row(risks, cutoff))

    return pd.DataFrame(summaries, columns=SUMMARY_COLUMNS)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the historical backfill against an explicitly selectable DuckDB file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--periods", type=int, default=8)
    parser.add_argument("--db-path", type=Path, default=database_path())
    args = parser.parse_args(argv)

    connection = duckdb.connect(str(args.db_path))
    try:
        summary = backfill_risk(connection, periods=args.periods)
    finally:
        connection.close()
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
