"""Deterministic, read-only supplier-outage simulation.

Exposure is an upper-bound estimate rather than an accounting valuation. Incremental
material gaps are allocated to SKUs by demand share, converted to finished units through
the BOM, and collapsed to the maximum constrained units per SKU (the bottleneck rule) to
avoid double counting the same SKU across multiple worsened materials.

Simulation fidelity is bounded by the 28-day forecast horizon: receipts pushed past
``T+28`` are indistinguishable from never arriving, so outages longer than the horizon
saturate to the 28-day result. The API therefore caps ``days`` at 28.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import pandas as pd

from .backfill import _expand_material_demand, _load_bom
from .risk import (
    calculate_material_risk,
    evaluation_date,
    load_future_purchase_orders,
    load_latest_inventory,
    load_supply_profile,
)

LEVEL_RANK = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}


@dataclass(frozen=True)
class WhatIfSummary:
    baseline_red_count: int
    baseline_orange_count: int
    new_red_count: int
    new_orange_count: int
    total_gap_delta: int
    affected_sku_count: int
    exposure_amount: float


@dataclass(frozen=True)
class WorsenedMaterial:
    material_pn: str
    baseline_level: str
    scenario_level: str
    baseline_gap: int
    scenario_gap: int
    gap_delta: int
    split_pct: float


@dataclass(frozen=True)
class AffectedSku:
    sku_id: str
    affected_units: float
    unit_price: float
    exposure_amount: float


@dataclass(frozen=True)
class WhatIfResult:
    summary: WhatIfSummary
    worsened_materials: list[WorsenedMaterial]
    affected_skus: list[AffectedSku]
    baseline_risks: pd.DataFrame = field(repr=False, compare=False)
    scenario_risks: pd.DataFrame = field(repr=False, compare=False)


def _load_forecast(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    forecast = connection.execute(
        "SELECT sku_id, date, CAST(yhat AS DOUBLE) AS yhat FROM forecast_daily "
        "WHERE model_name = 'lightgbm' ORDER BY sku_id, date"
    ).fetchdf()
    forecast["date"] = pd.to_datetime(forecast["date"])
    forecast["yhat"] = forecast["yhat"].astype(float)
    return forecast


def _delay_supplier_orders(
    connection: duckdb.DuckDBPyConnection,
    purchase_orders: pd.DataFrame,
    supplier_id: str,
    eval_date: pd.Timestamp,
    days: int,
) -> pd.DataFrame:
    window_end = eval_date + pd.Timedelta(days=days)
    delayed = connection.execute(
        "SELECT material_pn, eta_date, SUM(qty)::DOUBLE AS qty FROM open_po "
        "WHERE supplier_id = ? AND eta_date > ? AND eta_date <= ? "
        "GROUP BY material_pn, eta_date ORDER BY material_pn, eta_date",
        [supplier_id, eval_date.date(), window_end.date()],
    ).fetchdf()
    if delayed.empty:
        return purchase_orders.copy()
    delayed["eta_date"] = pd.to_datetime(delayed["eta_date"])
    removals = delayed.assign(qty=-delayed["qty"])
    arrivals = delayed.assign(eta_date=delayed["eta_date"] + pd.Timedelta(days=days))
    return (
        pd.concat([purchase_orders, removals, arrivals], ignore_index=True)
        .groupby(["material_pn", "eta_date"], as_index=False, observed=True)["qty"]
        .sum()
        .sort_values(["material_pn", "eta_date"])
        .reset_index(drop=True)
    )


def _affected_skus(
    connection: duckdb.DuckDBPyConnection,
    forecast: pd.DataFrame,
    bom: pd.DataFrame,
    gap_deltas: pd.DataFrame,
    eval_date: pd.Timestamp,
    days: int,
) -> list[AffectedSku]:
    positive = gap_deltas.loc[gap_deltas["gap_delta"] > 0, ["material_pn", "gap_delta"]]
    if positive.empty:
        return []
    window_end = eval_date + pd.Timedelta(days=days)
    expanded = forecast.merge(bom, on="sku_id", how="inner")
    expanded = expanded.loc[
        (expanded["date"] > eval_date) & (expanded["date"] <= window_end)
    ].copy()
    expanded["material_demand"] = expanded["yhat"] * expanded["qty_per_unit"]
    shares = (
        expanded.groupby(
            ["material_pn", "sku_id", "qty_per_unit"], as_index=False, observed=True
        )["material_demand"]
        .sum()
        .merge(positive, on="material_pn", how="inner")
    )
    totals = shares.groupby("material_pn", observed=True)["material_demand"].transform("sum")
    shares = shares.loc[totals > 0].copy()
    shares["affected_units"] = (
        shares["gap_delta"] * shares["material_demand"] / totals.loc[shares.index]
    ) / shares["qty_per_unit"]
    bottlenecks = (
        shares.groupby("sku_id", as_index=False, observed=True)["affected_units"]
        .max()
        .sort_values("sku_id")
    )
    prices = connection.execute(
        "SELECT sku_id, CAST(sell_price AS DOUBLE) AS unit_price FROM prices "
        "WHERE week_start <= ? "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY week_start DESC) = 1",
        [eval_date.date()],
    ).fetchdf()
    priced = bottlenecks.merge(prices, on="sku_id", how="left")
    priced["unit_price"] = priced["unit_price"].fillna(0.0)
    priced["exposure_amount"] = priced["affected_units"] * priced["unit_price"]
    priced = priced.sort_values(["exposure_amount", "sku_id"], ascending=[False, True])
    return [
        AffectedSku(
            sku_id=str(row.sku_id),
            affected_units=round(float(row.affected_units), 2),
            unit_price=round(float(row.unit_price), 2),
            exposure_amount=round(float(row.exposure_amount), 2),
        )
        for row in priced.itertuples(index=False)
    ]


def simulate_supplier_outage(
    connection: duckdb.DuckDBPyConnection, supplier_id: str, days: int
) -> WhatIfResult:
    """Simulate delaying one supplier's receipts in ``(T, T+days]`` entirely in memory."""
    exists = connection.execute(
        "SELECT count(*) FROM suppliers WHERE supplier_id = ?", [supplier_id]
    ).fetchone()[0]
    if not exists:
        raise ValueError(f"Supplier {supplier_id} not found")

    eval_date = evaluation_date(connection)
    forecast = _load_forecast(connection)
    bom = _load_bom(connection)
    demand = _expand_material_demand(forecast, bom)
    inventory = load_latest_inventory(connection, eval_date)
    purchase_orders = load_future_purchase_orders(connection, eval_date)
    supply_profile = load_supply_profile(connection)
    scenario_orders = _delay_supplier_orders(
        connection, purchase_orders, supplier_id, eval_date, days
    )
    baseline = calculate_material_risk(
        demand, inventory, purchase_orders, supply_profile, eval_date
    )
    scenario = calculate_material_risk(
        demand, inventory, scenario_orders, supply_profile, eval_date
    )

    compared = baseline[["material_pn", "risk_level", "gap_qty"]].merge(
        scenario[["material_pn", "risk_level", "gap_qty"]],
        on="material_pn",
        suffixes=("_baseline", "_scenario"),
        validate="one_to_one",
    )
    compared["gap_delta"] = compared["gap_qty_scenario"] - compared["gap_qty_baseline"]
    if (compared["gap_delta"] < 0).any():
        raise AssertionError("Supplier outage unexpectedly improved a material gap")
    compared["level_delta"] = compared["risk_level_scenario"].map(LEVEL_RANK) - compared[
        "risk_level_baseline"
    ].map(LEVEL_RANK)
    worsened = compared.loc[(compared["level_delta"] > 0) | (compared["gap_delta"] > 0)].copy()
    split = connection.execute(
        "SELECT material_pn, CAST(split_pct AS DOUBLE) AS split_pct FROM supply_split "
        "WHERE supplier_id = ?",
        [supplier_id],
    ).fetchdf()
    worsened = worsened.merge(split, on="material_pn", how="left").fillna({"split_pct": 0.0})
    worsened = worsened.sort_values(["gap_delta", "material_pn"], ascending=[False, True])
    material_rows = [
        WorsenedMaterial(
            material_pn=str(row.material_pn),
            baseline_level=str(row.risk_level_baseline),
            scenario_level=str(row.risk_level_scenario),
            baseline_gap=int(row.gap_qty_baseline),
            scenario_gap=int(row.gap_qty_scenario),
            gap_delta=int(row.gap_delta),
            split_pct=round(float(row.split_pct), 2),
        )
        for row in worsened.itertuples(index=False)
    ]
    sku_rows = _affected_skus(connection, forecast, bom, worsened, eval_date, days)
    summary = WhatIfSummary(
        baseline_red_count=int((baseline["risk_level"] == "RED").sum()),
        baseline_orange_count=int((baseline["risk_level"] == "ORANGE").sum()),
        new_red_count=int(
            ((compared["risk_level_scenario"] == "RED") & (compared["risk_level_baseline"] != "RED")).sum()
        ),
        new_orange_count=int(
            ((compared["risk_level_scenario"] == "ORANGE")
             & compared["risk_level_baseline"].isin(["GREEN", "YELLOW"])).sum()
        ),
        total_gap_delta=int(compared["gap_delta"].sum()),
        affected_sku_count=len(sku_rows),
        exposure_amount=round(sum(row.exposure_amount for row in sku_rows), 2),
    )
    return WhatIfResult(summary, material_rows, sku_rows, baseline, scenario)
