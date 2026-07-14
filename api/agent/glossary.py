"""Load prompt domain knowledge from the project data dictionary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GLOSSARY_PATH = REPO_ROOT / "docs" / "01_数据字典.md"


@dataclass(frozen=True)
class Term:
    """One business term parsed from the Markdown source of truth."""

    term: str
    definition: str
    calculation: str


def _markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_glossary(path: str | Path | None = None) -> list[Term]:
    """Parse the section 4 terminology table, failing fast on incomplete input."""
    source = Path(path) if path is not None else DEFAULT_GLOSSARY_PATH
    if not source.is_file():
        raise FileNotFoundError(f"Glossary source does not exist: {source}")

    lines = source.read_text(encoding="utf-8").splitlines()
    in_section = False
    table_started = False
    terms: list[Term] = []
    for line in lines:
        if line.startswith("## 4."):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        cells = _markdown_cells(line) if line.lstrip().startswith("|") else []
        if cells[:3] == ["术语", "定义", "计算式 / 落点"]:
            table_started = True
            continue
        if not table_started or len(cells) != 3:
            continue
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        terms.append(Term(*cells))

    if len(terms) < 8:
        raise ValueError(f"Glossary must contain at least 8 terms; parsed {len(terms)} from {source}")
    return terms


def render_glossary(terms: list[Term]) -> str:
    """Render terms as a compact, deterministic prompt block."""
    return "\n".join(
        f"- {item.term}: {item.definition}; 计算/落点: {item.calculation}" for item in terms
    )


SCHEMA_OBJECTS: Final[frozenset[str]] = frozenset(
    {
        "sales_daily",
        "calendar",
        "prices",
        "products",
        "materials",
        "bom",
        "suppliers",
        "supply_split",
        "inventory_onhand",
        "open_po",
        "forecast_daily",
        "forecast_metrics",
        "material_risk",
        "v_material_demand_daily",
        "v_risk_by_commodity",
        "v_risk_by_item_group",
        "v_risk_by_supplier",
    }
)

SCHEMA_CARDS: Final[str] = """ChainPilot DuckDB schema cards (only these objects may be queried):

sales_daily -- finished-product daily actual sales/demand.
  sku_id VARCHAR: product id, join products.sku_id; date DATE: sales date, join calendar.date; units_sold INTEGER: daily units sold. Composite key (sku_id, date).
calendar -- calendar and event attributes.
  date DATE: primary key, join sales_daily.date; weekday VARCHAR: weekday; event_name VARCHAR NULL: holiday/event; is_weekend BOOLEAN: weekend flag.
prices -- weekly product selling price.
  sku_id VARCHAR: join products.sku_id; week_start DATE: week start; sell_price DECIMAL(8,2): weekly price.
products -- finished-product master data.
  sku_id VARCHAR: primary key and join key for sales_daily/prices/bom/forecast_daily; product_name VARCHAR: readable name; product_family VARCHAR: family such as SNACKS or BEVERAGE.
materials -- material master data.
  material_pn VARCHAR: primary key and join key for bom/supply_split/inventory_onhand/open_po/material_risk; material_name VARCHAR: readable name; commodity VARCHAR: PACKAGING/RAW_FOOD/ADDITIVE/LABEL/CONTAINER; item_group VARCHAR: second-level category; unit_cost DECIMAL(8,4): unit cost.
bom -- single-level bill of materials.
  sku_id VARCHAR: join products.sku_id; material_pn VARCHAR: join materials.material_pn; qty_per_unit DECIMAL(8,3): material quantity per product unit. Composite key (sku_id, material_pn).
suppliers -- supplier master data.
  supplier_id VARCHAR: primary key and join key for supply_split/open_po; supplier_name VARCHAR: fictional name; region VARCHAR: CN/SEA/US/EU.
supply_split -- material sourcing shares and terms.
  material_pn VARCHAR: join materials.material_pn; supplier_id VARCHAR: join suppliers.supplier_id; split_pct DECIMAL(5,2): sourcing share, totals 100 per material; lead_time_days INTEGER: lead time (7-90 days); moq INTEGER: minimum order quantity. Composite key (material_pn, supplier_id).
inventory_onhand -- weekly material inventory snapshots.
  material_pn VARCHAR: join materials.material_pn; snapshot_date DATE: snapshot date (latest is current inventory); qty_onhand INTEGER: on-hand quantity. Composite key (material_pn, snapshot_date).
open_po -- open/in-transit purchase orders.
  po_id VARCHAR: primary key; material_pn VARCHAR: join materials.material_pn; supplier_id VARCHAR: join suppliers.supplier_id; qty INTEGER: order quantity; eta_date DATE: expected arrival date.
forecast_daily -- future 28-day product demand forecast.
  sku_id VARCHAR: join products.sku_id; date DATE: forecast date; model_name VARCHAR: seasonal_naive/ets/lightgbm; yhat DECIMAL(10,2): forecast units. Composite key (sku_id, date, model_name).
forecast_metrics -- rolling-backtest model scorecard.
  model_name VARCHAR: seasonal_naive/ets/lightgbm; fold INTEGER: backtest fold; mape DECIMAL: MAPE; wrmsse DECIMAL: WRMSSE.
material_risk -- dated material risk results.
  material_pn VARCHAR: join materials.material_pn; eval_date DATE: evaluation date; doi_days DECIMAL(6,1): days of inventory; lt_coverage DECIMAL(5,2): DOI/effective lead time; supplier_concentration DECIMAL(5,2): maximum supplier share; gap_qty INTEGER: projected shortage (0 means none); gap_date DATE NULL: first shortage date; risk_level VARCHAR: RED/ORANGE/YELLOW/GREEN; risk_reasons VARCHAR: semicolon-separated rule codes. Composite key (material_pn, eval_date).
v_material_demand_daily -- LightGBM forecast expanded through BOM to daily material demand.
  material_pn VARCHAR: join materials/material_risk; date DATE: demand date; demand_qty DECIMAL: sum(yhat * qty_per_unit).
v_risk_by_commodity -- dated risk counts and shortage aggregated by commodity.
  eval_date DATE: join material_risk.eval_date; commodity VARCHAR: materials commodity; red_count/orange_count/yellow_count/green_count BIGINT: level counts; total_gap_qty HUGEINT: summed gap.
v_risk_by_item_group -- dated risk counts and shortage aggregated by item group.
  eval_date DATE: join material_risk.eval_date; item_group VARCHAR: materials item group; red_count/orange_count/yellow_count/green_count BIGINT: level counts; total_gap_qty HUGEINT: summed gap.
v_risk_by_supplier -- dated severe-risk exposure aggregated by supplier.
  eval_date DATE: join material_risk.eval_date; supplier_id VARCHAR: join suppliers.supplier_id; supplier_name VARCHAR: supplier name; red_orange_material_count BIGINT: distinct RED/ORANGE material count; weighted_gap_qty DECIMAL: gap_qty weighted by split_pct/100.
"""
