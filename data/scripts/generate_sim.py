"""Generate the deterministic simulated manufacturing layer for ChainPilot."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

SEED = 42
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROCESSED_DIR = REPO_ROOT / "data" / "processed"
DEFAULT_GROUND_TRUTH = REPO_ROOT / "data" / "ground_truth_scenarios.json"
SIM_TABLES = (
    "products",
    "materials",
    "bom",
    "suppliers",
    "supply_split",
    "inventory_onhand",
    "open_po",
)
COMMODITY_GROUPS = {
    "PACKAGING": ("CARTON", "FILM", "TRAY"),
    "RAW_FOOD": ("GRAIN", "OIL", "PROTEIN"),
    "ADDITIVE": ("FLAVOR", "COLOR", "PRESERVATIVE"),
    "LABEL": ("PRIMARY_LABEL", "CASE_LABEL"),
    "CONTAINER": ("BOTTLE", "JAR", "POUCH"),
}
FAMILIES = ("SNACKS", "BEVERAGE", "PANTRY", "FROZEN")


def write_parquet(frame: pd.DataFrame, path: Path, order_by: str) -> None:
    """Write parquet deterministically using a stable row order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    try:
        connection.register("frame", frame)
        connection.execute(
            f"COPY (SELECT * FROM frame ORDER BY {order_by}) TO ? "
            "(FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(path)],
        )
    finally:
        connection.close()


def load_sales_profile(processed_dir: Path) -> tuple[pd.DataFrame, date]:
    """Load the selected SKUs, average demand, and latest demand date."""
    sales_path = processed_dir / "sales_daily.parquet"
    if not sales_path.is_file():
        raise FileNotFoundError(f"缺少 {sales_path}")
    connection = duckdb.connect()
    try:
        profile = connection.execute(
            "SELECT sku_id, AVG(units_sold)::DOUBLE AS avg_daily_units "
            "FROM read_parquet(?) GROUP BY sku_id ORDER BY sku_id",
            [str(sales_path)],
        ).fetchdf()
        latest = connection.execute(
            "SELECT MAX(date)::DATE FROM read_parquet(?)", [str(sales_path)]
        ).fetchone()[0]
    finally:
        connection.close()
    if profile.empty or latest is None:
        raise ValueError("sales_daily.parquet 没有可用销量")
    return profile, latest


def build_products(profile: pd.DataFrame) -> pd.DataFrame:
    """Create readable product master data for every selected SKU."""
    rows = []
    for index, sku_id in enumerate(profile["sku_id"].tolist(), start=1):
        family = FAMILIES[(index - 1) % len(FAMILIES)]
        rows.append((sku_id, f"Contoso {family.title()} {index:03d}", family))
    return pd.DataFrame(rows, columns=["sku_id", "product_name", "product_family"])


def build_materials(rng: np.random.Generator, count: int = 300) -> pd.DataFrame:
    """Create material master data across the documented commodities."""
    commodities = tuple(COMMODITY_GROUPS)
    rows = []
    for index in range(1, count + 1):
        commodity = commodities[(index - 1) % len(commodities)]
        groups = COMMODITY_GROUPS[commodity]
        item_group = groups[(index - 1) % len(groups)]
        unit_cost = round(float(rng.uniform(0.015, 12.0)), 4)
        rows.append(
            (f"PN-{index:05d}", f"{item_group.replace('_', ' ').title()} {index:03d}", commodity, item_group, unit_cost)
        )
    return pd.DataFrame(
        rows, columns=["material_pn", "material_name", "commodity", "item_group", "unit_cost"]
    )


def build_bom(
    rng: np.random.Generator, products: pd.DataFrame, materials: pd.DataFrame
) -> pd.DataFrame:
    """Create 3–8 BOM rows per SKU with exactly 20% shared materials."""
    sku_ids = products["sku_id"].tolist()
    material_ids = materials["material_pn"].tolist()
    shared = material_ids[:60]
    dedicated = material_ids[60:]
    target_counts = rng.integers(3, 9, size=len(sku_ids))
    while int(target_counts.sum()) < len(dedicated) + 2 * len(shared):
        candidates = np.flatnonzero(target_counts < 8)
        target_counts[int(rng.choice(candidates))] += 1

    assignments: dict[str, list[str]] = {sku: [] for sku in sku_ids}
    for index, material_pn in enumerate(dedicated):
        sku_id = sku_ids[index % len(sku_ids)]
        assignments[sku_id].append(material_pn)
    for index, material_pn in enumerate(shared):
        first = index % len(sku_ids)
        second = (index + len(FAMILIES)) % len(sku_ids)
        assignments[sku_ids[first]].append(material_pn)
        assignments[sku_ids[second]].append(material_pn)

    for sku_index, sku_id in enumerate(sku_ids):
        available = [material for material in shared if material not in assignments[sku_id]]
        needed = int(target_counts[sku_index]) - len(assignments[sku_id])
        if needed > 0:
            chosen = rng.choice(available, size=needed, replace=False).tolist()
            assignments[sku_id].extend(chosen)

    rows = []
    for sku_id in sku_ids:
        for material_pn in sorted(assignments[sku_id]):
            qty = round(float(rng.uniform(0.05, 2.5)), 3)
            rows.append((sku_id, material_pn, qty))
    return pd.DataFrame(rows, columns=["sku_id", "material_pn", "qty_per_unit"])


def build_suppliers() -> pd.DataFrame:
    """Create forty fictional suppliers."""
    regions = ("CN", "SEA", "US", "EU")
    rows = [
        (f"SUP-{index:03d}", f"Contoso Supply Partner {index:03d}", regions[(index - 1) % 4])
        for index in range(1, 41)
    ]
    return pd.DataFrame(rows, columns=["supplier_id", "supplier_name", "region"])


def build_supply_split(
    rng: np.random.Generator,
    materials: pd.DataFrame,
    suppliers: pd.DataFrame,
    single_scenarios: set[str],
    long_lead_scenarios: set[str],
) -> pd.DataFrame:
    """Create exact 60% dual, 30% single, and 10% triple sourcing."""
    material_ids = materials["material_pn"].tolist()
    supplier_ids = suppliers["supplier_id"].tolist()
    source_counts = np.array([1] * 90 + [2] * 180 + [3] * 30)
    rng.shuffle(source_counts)
    for material_pn in sorted(single_scenarios):
        index = material_ids.index(material_pn)
        if source_counts[index] != 1:
            swap_index = int(np.flatnonzero(source_counts == 1)[0])
            source_counts[index], source_counts[swap_index] = source_counts[swap_index], source_counts[index]

    rows = []
    for material_pn, source_count in zip(material_ids, source_counts, strict=True):
        selected = rng.choice(supplier_ids, size=int(source_count), replace=False).tolist()
        if source_count == 1:
            splits = [100.0]
        elif source_count == 2:
            first = int(rng.integers(30, 71))
            splits = [float(first), float(100 - first)]
        else:
            first = int(rng.integers(20, 56))
            second = int(rng.integers(15, 86 - first))
            splits = [float(first), float(second), float(100 - first - second)]
        for supplier_id, split_pct in zip(selected, splits, strict=True):
            lead_time = int(rng.integers(7, 91))
            if material_pn in long_lead_scenarios:
                lead_time = int(rng.integers(75, 91))
            rows.append(
                (material_pn, supplier_id, split_pct, lead_time, int(rng.integers(100, 5001)))
            )
    return pd.DataFrame(
        rows,
        columns=["material_pn", "supplier_id", "split_pct", "lead_time_days", "moq"],
    )


def material_daily_demand(profile: pd.DataFrame, bom: pd.DataFrame) -> pd.Series:
    """Calculate historical average daily component demand from the BOM."""
    demand = bom.merge(profile, on="sku_id", how="left", validate="many_to_one")
    demand["daily_demand"] = demand["qty_per_unit"] * demand["avg_daily_units"]
    return demand.groupby("material_pn")["daily_demand"].sum()


def build_inventory(
    rng: np.random.Generator,
    material_ids: list[str],
    demand: pd.Series,
    latest_date: date,
    scenario_ids: set[str],
) -> pd.DataFrame:
    """Create 156 weekly snapshots sized to roughly 15–90 DOI for normal materials."""
    snapshot_dates = [latest_date - timedelta(weeks=week) for week in range(155, -1, -1)]
    rows = []
    for material_pn in material_ids:
        daily = max(float(demand.get(material_pn, 0.0)), 0.1)
        normal_doi = float(rng.uniform(15.0, 90.0))
        baseline = daily * normal_doi
        for position, snapshot_date in enumerate(snapshot_dates):
            seasonal = 1.0 + 0.12 * np.sin(position * 2.0 * np.pi / 52.0)
            noise = float(rng.uniform(0.9, 1.1))
            quantity = max(0, int(round(baseline * seasonal * noise)))
            if material_pn in scenario_ids and position == len(snapshot_dates) - 1:
                quantity = max(1, int(round(daily * float(rng.uniform(1.0, 3.5)))))
            rows.append((material_pn, snapshot_date, quantity))
    return pd.DataFrame(rows, columns=["material_pn", "snapshot_date", "qty_onhand"])


def build_open_po(
    rng: np.random.Generator,
    material_ids: list[str],
    supply_split: pd.DataFrame,
    demand: pd.Series,
    latest_date: date,
    insufficient_scenarios: set[str],
    all_scenarios: set[str],
) -> pd.DataFrame:
    """Create about 1,500 open orders while preserving injected shortage conditions."""
    normal_materials = [material for material in material_ids if material not in all_scenarios]
    rows = []
    for index in range(1, 1494):
        material_pn = str(rng.choice(normal_materials))
        candidates = supply_split.loc[
            supply_split["material_pn"].eq(material_pn), "supplier_id"
        ].tolist()
        supplier_id = str(rng.choice(candidates))
        quantity = int(rng.integers(100, 10001))
        eta = latest_date + timedelta(days=int(rng.integers(1, 121)))
        rows.append((f"PO-{index:06d}", material_pn, supplier_id, quantity, eta))

    next_index = 1494
    for material_pn in sorted(insufficient_scenarios):
        supplier_id = supply_split.loc[
            supply_split["material_pn"].eq(material_pn), "supplier_id"
        ].iloc[0]
        daily = max(float(demand.get(material_pn, 0.0)), 0.1)
        quantity = max(1, int(round(daily * 0.5)))
        eta = latest_date + timedelta(days=int(rng.integers(30, 61)))
        rows.append((f"PO-{next_index:06d}", material_pn, supplier_id, quantity, eta))
        next_index += 1
    return pd.DataFrame(rows, columns=["po_id", "material_pn", "supplier_id", "qty", "eta_date"])


def generate_sim(processed_dir: Path, ground_truth_path: Path) -> None:
    """Generate every simulation table and the ten documented ground-truth scenarios."""
    rng = np.random.default_rng(SEED)
    profile, latest_date = load_sales_profile(processed_dir)
    products = build_products(profile)
    materials = build_materials(rng)
    bom = build_bom(rng, products, materials)
    suppliers = build_suppliers()

    shared_counts = bom.groupby("material_pn")["sku_id"].nunique()
    shared_ids = sorted(shared_counts.loc[shared_counts > 1].index.tolist())
    material_ids = materials["material_pn"].tolist()
    single_scenarios = set(material_ids[210:214])
    shared_scenarios = set(shared_ids[:3])
    long_lead_scenarios = set(material_ids[270:273])
    all_scenarios = single_scenarios | shared_scenarios | long_lead_scenarios
    insufficient_scenarios = single_scenarios | shared_scenarios

    supply_split = build_supply_split(
        rng, materials, suppliers, single_scenarios, long_lead_scenarios
    )
    demand = material_daily_demand(profile, bom)
    inventory = build_inventory(rng, material_ids, demand, latest_date, all_scenarios)
    open_po = build_open_po(
        rng,
        material_ids,
        supply_split,
        demand,
        latest_date,
        insufficient_scenarios,
        all_scenarios,
    )

    table_frames = {
        "products": (products, "sku_id"),
        "materials": (materials, "material_pn"),
        "bom": (bom, "sku_id, material_pn"),
        "suppliers": (suppliers, "supplier_id"),
        "supply_split": (supply_split, "material_pn, supplier_id"),
        "inventory_onhand": (inventory, "material_pn, snapshot_date"),
        "open_po": (open_po, "po_id"),
    }
    for table_name, (frame, order_by) in table_frames.items():
        write_parquet(frame, processed_dir / f"{table_name}.parquet", order_by)

    scenarios = []
    scenario_groups = (
        (sorted(single_scenarios), "单源+低库存+在途不足", "未来 1–4 天"),
        (sorted(shared_scenarios), "共用料高需求+库存薄", "未来 1–4 天"),
        (sorted(long_lead_scenarios), "长交期+零在途", "未来 1–4 天"),
    )
    scenario_number = 1
    for material_group, construction, window in scenario_groups:
        for material_pn in material_group:
            scenarios.append(
                {
                    "scenario_id": f"GT-{scenario_number:02d}",
                    "material_pn": material_pn,
                    "construction": construction,
                    "expected_gap_window": window,
                }
            )
            scenario_number += 1
    ground_truth_path.parent.mkdir(parents=True, exist_ok=True)
    ground_truth_path.write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "模拟层完成: "
        + ", ".join(f"{name}={len(frame)}" for name, (frame, _) in table_frames.items())
        + f", scenarios={len(scenarios)}"
    )


def parse_args() -> argparse.Namespace:
    """Parse optional paths used by tests and standalone runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--ground-truth-path", type=Path, default=DEFAULT_GROUND_TRUTH)
    return parser.parse_args()


def main() -> None:
    """Generate deterministic simulation artifacts."""
    args = parse_args()
    generate_sim(args.processed_dir, args.ground_truth_path)


if __name__ == "__main__":
    main()
