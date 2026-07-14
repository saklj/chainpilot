"""MRP-lite material-risk calculations and deterministic DuckDB persistence.

The evaluation date is the maximum ``sales_daily.date`` (T), and the forecast window is
T+1 through T+28. Inventory is the latest snapshot on or before T. The effective lead
time is the minimum supplier lead time. Purchase orders arriving after T enter the
projected balance on their ETA; older orders are treated as already received, while
orders beyond T+28 naturally cannot affect the forecast-window balance.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

if __package__:
    from .mrp import Contributor, load_material_demand, top_sku_contributors
else:
    from mrp import Contributor, load_material_demand, top_sku_contributors

REPO_ROOT = Path(__file__).resolve().parents[2]
HORIZON = 28
NO_DEMAND_DOI = 999.0  # Sentinel: the current inventory has no finite consumption horizon.
RISK_COLUMNS = (
    "material_pn",
    "eval_date",
    "doi_days",
    "lt_coverage",
    "supplier_concentration",
    "gap_qty",
    "gap_date",
    "risk_level",
    "risk_reasons",
)
REASON_ORDER = (
    "GAP_BEFORE_LT",
    "GAP_IN_HORIZON",
    "LOW_DOI_SINGLE_SOURCE",
    "LOW_DOI",
    "HIGH_CONCENTRATION",
)


def database_path() -> Path:
    """Resolve DUCKDB_PATH relative to the repository root."""
    configured = Path(os.environ.get("DUCKDB_PATH", "data/chainpilot.duckdb"))
    return configured if configured.is_absolute() else REPO_ROOT / configured


def evaluation_date(connection: duckdb.DuckDBPyConnection) -> pd.Timestamp:
    """Return T, the latest actual-sales date."""
    value = connection.execute("SELECT MAX(date) FROM sales_daily").fetchone()[0]
    if value is None:
        raise ValueError("sales_daily is empty")
    return pd.Timestamp(value)


def load_latest_inventory(
    connection: duckdb.DuckDBPyConnection, eval_date: pd.Timestamp
) -> pd.DataFrame:
    """Load each material's latest inventory snapshot on or before T."""
    frame = connection.execute(
        "SELECT material_pn, snapshot_date, qty_onhand FROM inventory_onhand "
        "WHERE snapshot_date <= ? "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY material_pn ORDER BY snapshot_date DESC) = 1 "
        "ORDER BY material_pn",
        [eval_date.date()],
    ).fetchdf()
    frame["snapshot_date"] = pd.to_datetime(frame["snapshot_date"])
    return frame


def load_future_purchase_orders(
    connection: duckdb.DuckDBPyConnection, eval_date: pd.Timestamp
) -> pd.DataFrame:
    """Load purchase orders whose ETA is after T."""
    frame = connection.execute(
        "SELECT material_pn, eta_date, qty FROM open_po "
        "WHERE eta_date > ? ORDER BY material_pn, eta_date, po_id",
        [eval_date.date()],
    ).fetchdf()
    frame["eta_date"] = pd.to_datetime(frame["eta_date"])
    return frame


def load_supply_profile(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Aggregate effective lead time, concentration, and source count by material."""
    return connection.execute(
        "SELECT material_pn, MIN(lead_time_days)::INTEGER AS min_lt, "
        "MAX(split_pct)::DOUBLE AS supplier_concentration, "
        "COUNT(*)::INTEGER AS supplier_count, "
        "arg_max(supplier_id, split_pct) AS primary_supplier_id "
        "FROM supply_split GROUP BY material_pn ORDER BY material_pn"
    ).fetchdf()


def projected_shortage(
    demand: pd.DataFrame,
    purchase_orders: pd.DataFrame,
    initial_inventory: float,
    eval_date: pd.Timestamp,
    horizon: int = HORIZON,
) -> tuple[pd.Timestamp | None, int]:
    """Roll inventory daily and return the first negative date and deepest shortage.

    Receipts are applied before same-day demand, so an arrival that exactly replenishes the
    required quantity prevents a shortage on that date. Fractional material demand is kept
    throughout the balance calculation; the final shortage is rounded up to avoid
    understating the integer quantity that must be recovered.
    """
    dates = pd.date_range(eval_date + pd.Timedelta(days=1), periods=horizon, freq="D")
    daily_demand = demand.groupby("date", observed=True)["demand_qty"].sum().reindex(
        dates, fill_value=0.0
    )
    if purchase_orders.empty:
        daily_receipts = pd.Series(0.0, index=dates)
    else:
        daily_receipts = (
            purchase_orders.groupby("eta_date", observed=True)["qty"]
            .sum()
            .reindex(dates, fill_value=0.0)
        )

    balance = float(initial_inventory)
    gap_date: pd.Timestamp | None = None
    deepest_balance = 0.0
    for date in dates:
        balance += float(daily_receipts.loc[date]) - float(daily_demand.loc[date])
        if balance < 0 and gap_date is None:
            gap_date = pd.Timestamp(date)
        deepest_balance = min(deepest_balance, balance)
    gap_qty = int(math.ceil(abs(deepest_balance))) if deepest_balance < 0 else 0
    return gap_date, gap_qty


def classify_risk(
    *,
    eval_date: pd.Timestamp,
    gap_date: pd.Timestamp | None,
    gap_qty: int,
    doi_days: float,
    min_lt: int,
    supplier_count: int,
    supplier_concentration: float,
) -> tuple[str, str]:
    """Apply every documented rule and return the highest level plus all reason codes."""
    gap_before_lt = bool(
        gap_qty > 0
        and gap_date is not None
        and (pd.Timestamp(gap_date) - eval_date).days < min_lt
    )
    gap_in_horizon = gap_qty > 0 and not gap_before_lt
    low_doi_single = doi_days < 0.5 * min_lt and supplier_count == 1
    low_doi = doi_days < min_lt
    high_concentration = supplier_concentration > 70 and doi_days < 1.5 * min_lt
    matches = {
        "GAP_BEFORE_LT": gap_before_lt,
        "GAP_IN_HORIZON": gap_in_horizon,
        "LOW_DOI_SINGLE_SOURCE": low_doi_single,
        "LOW_DOI": low_doi,
        "HIGH_CONCENTRATION": high_concentration,
    }
    reasons = ";".join(code for code in REASON_ORDER if matches[code])

    if gap_before_lt:
        level = "RED"
    elif gap_in_horizon or low_doi_single:
        level = "ORANGE"
    elif low_doi or high_concentration:
        level = "YELLOW"
    else:
        level = "GREEN"
    return level, reasons


def calculate_material_risk(
    demand: pd.DataFrame,
    inventory: pd.DataFrame,
    purchase_orders: pd.DataFrame,
    supply_profile: pd.DataFrame,
    eval_date: pd.Timestamp,
) -> pd.DataFrame:
    """Calculate risk and explanation metadata for every material in demand."""
    forecast_start = eval_date + pd.Timedelta(days=1)
    forecast_end = eval_date + pd.Timedelta(days=HORIZON)
    window_demand = demand.loc[
        (demand["date"] >= forecast_start) & (demand["date"] <= forecast_end)
    ].copy()
    inventory_by_material = inventory.set_index("material_pn")
    supply_by_material = supply_profile.set_index("material_pn")
    rows: list[dict[str, Any]] = []
    for material_pn, material_demand in window_demand.groupby("material_pn", sort=True):
        if material_pn not in inventory_by_material.index:
            raise ValueError(f"Missing inventory for {material_pn}")
        if material_pn not in supply_by_material.index:
            raise ValueError(f"Missing supply profile for {material_pn}")
        inventory_row = inventory_by_material.loc[material_pn]
        supply_row = supply_by_material.loc[material_pn]
        inventory_qty = float(inventory_row["qty_onhand"])
        min_lt = int(supply_row["min_lt"])
        supplier_count = int(supply_row["supplier_count"])
        concentration = float(supply_row["supplier_concentration"])
        average_demand = float(material_demand["demand_qty"].sum()) / HORIZON
        doi_days = NO_DEMAND_DOI if average_demand == 0 else round(inventory_qty / average_demand, 1)
        lt_coverage = round(doi_days / min_lt, 2)
        material_orders = purchase_orders.loc[purchase_orders["material_pn"] == material_pn]
        gap_date, gap_qty = projected_shortage(
            material_demand, material_orders, inventory_qty, eval_date
        )
        risk_level, risk_reasons = classify_risk(
            eval_date=eval_date,
            gap_date=gap_date,
            gap_qty=gap_qty,
            doi_days=doi_days,
            min_lt=min_lt,
            supplier_count=supplier_count,
            supplier_concentration=concentration,
        )
        rows.append(
            {
                "material_pn": str(material_pn),
                "eval_date": eval_date,
                "doi_days": doi_days,
                "lt_coverage": lt_coverage,
                "supplier_concentration": concentration,
                "gap_qty": gap_qty,
                "gap_date": gap_date,
                "risk_level": risk_level,
                "risk_reasons": risk_reasons,
                "inventory_qty": int(inventory_qty),
                "min_lt": min_lt,
                "supplier_count": supplier_count,
                "primary_supplier_id": str(supply_row["primary_supplier_id"]),
            }
        )
    if not rows:
        raise ValueError("No material demand exists in the 28-day forecast window")
    return pd.DataFrame(rows).sort_values("material_pn").reset_index(drop=True)


def _row_mapping(row: Mapping[str, Any] | pd.Series) -> Mapping[str, Any]:
    return row.to_dict() if isinstance(row, pd.Series) else row


def explain_risk(
    row: Mapping[str, Any] | pd.Series, contributors: Sequence[Contributor]
) -> str:
    """Render all risk reason codes and their computed numbers as one Chinese sentence."""
    values = _row_mapping(row)
    material_pn = str(values["material_pn"])
    reasons = [code for code in str(values.get("risk_reasons", "")).split(";") if code]
    doi_days = float(values["doi_days"])
    min_lt = int(values.get("min_lt", 0))
    concentration = float(values["supplier_concentration"])
    fragments: list[str] = []
    if "GAP_BEFORE_LT" in reasons:
        gap_date = pd.Timestamp(values["gap_date"]).date().isoformat()
        fragments.append(
            f"预计 {gap_date} 断料（现有库存仅够 {doi_days:.1f} 天，最短交期 "
            f"{min_lt} 天，现在追料已来不及），缺口约 {int(values['gap_qty']):,} 件"
        )
    if "GAP_IN_HORIZON" in reasons:
        gap_date = pd.Timestamp(values["gap_date"]).date().isoformat()
        fragments.append(
            f"预计 {gap_date} 出现缺口约 {int(values['gap_qty']):,} 件，"
            f"距评估日不少于最短交期 {min_lt} 天，仍可追料"
        )
    if "LOW_DOI_SINGLE_SOURCE" in reasons:
        supplier_id = str(values.get("primary_supplier_id", "未知供应商"))
        fragments.append(
            f"单源供应（{supplier_id} 占 {concentration:.0f}%），库存覆盖 "
            f"{doi_days:.1f} 天，不足最短交期一半"
        )
    if "LOW_DOI" in reasons:
        fragments.append(f"DOI {doi_days:.1f} 天低于最短交期 {min_lt} 天")
    if "HIGH_CONCENTRATION" in reasons:
        fragments.append(
            f"供应商集中度 {concentration:.0f}%，且 DOI 低于最短交期的 1.5 倍"
        )
    if not fragments:
        fragments.append("未命中缺口、低库存覆盖或高集中度风险规则")
    if contributors:
        sku_ids = [str(contributor["sku_id"]) for contributor in contributors]
        fragments.append(f"需求主要来自 {'、'.join(sku_ids)} 等 {len(sku_ids)} 个 SKU")
    return f"{material_pn}：" + "；".join(fragments) + "。"


def write_material_risk(
    connection: duckdb.DuckDBPyConnection, risks: pd.DataFrame, eval_date: pd.Timestamp
) -> None:
    """Replace the current evaluation date's material-risk rows."""
    persisted = risks[list(RISK_COLUMNS)].copy()
    connection.register("new_material_risk", persisted)
    connection.execute("DELETE FROM material_risk WHERE eval_date = ?", [eval_date.date()])
    connection.execute(
        "INSERT INTO material_risk "
        "SELECT material_pn, eval_date, doi_days, lt_coverage, supplier_concentration, "
        "gap_qty, gap_date, risk_level, risk_reasons FROM new_material_risk"
    )
    connection.unregister("new_material_risk")


def create_aggregation_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the documented commodity, item-group, and supplier risk views."""
    for view_name, dimension in (
        ("v_risk_by_commodity", "commodity"),
        ("v_risk_by_item_group", "item_group"),
    ):
        connection.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT r.eval_date, m.{dimension}, "
            "COUNT(*) FILTER (WHERE r.risk_level = 'RED') AS red_count, "
            "COUNT(*) FILTER (WHERE r.risk_level = 'ORANGE') AS orange_count, "
            "COUNT(*) FILTER (WHERE r.risk_level = 'YELLOW') AS yellow_count, "
            "COUNT(*) FILTER (WHERE r.risk_level = 'GREEN') AS green_count, "
            "SUM(r.gap_qty) AS total_gap_qty "
            "FROM material_risk r JOIN materials m USING (material_pn) "
            f"GROUP BY r.eval_date, m.{dimension}"
        )
    connection.execute(
        "CREATE OR REPLACE VIEW v_risk_by_supplier AS "
        "SELECT r.eval_date, s.supplier_id, s.supplier_name, "
        "COUNT(DISTINCT r.material_pn) FILTER "
        "(WHERE r.risk_level IN ('RED', 'ORANGE')) AS red_orange_material_count, "
        "SUM(CASE WHEN r.risk_level IN ('RED', 'ORANGE') "
        "THEN r.gap_qty * ss.split_pct / 100 ELSE 0 END) AS weighted_gap_qty "
        "FROM material_risk r JOIN supply_split ss USING (material_pn) "
        "JOIN suppliers s USING (supplier_id) "
        "GROUP BY r.eval_date, s.supplier_id, s.supplier_name"
    )


def run_risk_engine(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Calculate, persist, and aggregate material risk in one transaction."""
    eval_date = evaluation_date(connection)
    demand = load_material_demand(connection)
    inventory = load_latest_inventory(connection, eval_date)
    purchase_orders = load_future_purchase_orders(connection, eval_date)
    supply_profile = load_supply_profile(connection)
    risks = calculate_material_risk(
        demand, inventory, purchase_orders, supply_profile, eval_date
    )
    connection.execute("BEGIN TRANSACTION")
    try:
        write_material_risk(connection, risks, eval_date)
        create_aggregation_views(connection)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return risks


def print_summary(connection: duckdb.DuckDBPyConnection, risks: pd.DataFrame) -> None:
    """Print level totals, ground-truth recall details, and non-GT RED count."""
    counts = risks["risk_level"].value_counts()
    print("Risk level summary")
    for level in ("RED", "ORANGE", "YELLOW", "GREEN"):
        print(f"  {level}: {int(counts.get(level, 0))}")

    truth = json.loads((REPO_ROOT / "data" / "ground_truth_scenarios.json").read_text())
    indexed = risks.set_index("material_pn")
    print("\nGround-truth recall")
    print("scenario_id material_pn risk_level gap_date matched")
    matched_count = 0
    truth_materials = {scenario["material_pn"] for scenario in truth}
    for scenario in truth:
        row = indexed.loc[scenario["material_pn"]]
        matched = row["risk_level"] in {"RED", "ORANGE"} and int(row["gap_qty"]) > 0
        matched_count += int(matched)
        gap_date = "-" if pd.isna(row["gap_date"]) else pd.Timestamp(row["gap_date"]).date()
        print(
            f"{scenario['scenario_id']:>11} {scenario['material_pn']:>11} "
            f"{row['risk_level']:>10} {str(gap_date):>10} {matched}"
        )
    print(f"Recall: {matched_count}/{len(truth)}")
    false_red = risks.loc[
        (risks["risk_level"] == "RED") & ~risks["material_pn"].isin(truth_materials)
    ]
    print(f"Non-GT RED observation count: {len(false_red)}")

    # Exercise the reusable explanation path against computed values and DB contributors.
    for _, row in risks.loc[risks["risk_level"].isin(["RED", "ORANGE"])].head(3).iterrows():
        contributors = top_sku_contributors(connection, str(row["material_pn"]))
        explain_risk(row, contributors)


def main() -> None:
    """Run the complete MRP-lite material-risk workflow."""
    connection = duckdb.connect(str(database_path()))
    try:
        risks = run_risk_engine(connection)
        print_summary(connection, risks)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
