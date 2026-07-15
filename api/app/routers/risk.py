"""Read-only material-risk endpoints."""

from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from analytics.mrp import top_sku_contributors
from analytics.risk import explain_risk
from app.deps import get_db
from app.schemas import MaterialRisk, MaterialRiskDetail, RiskSummary

router = APIRouter(prefix="/api/risk", tags=["risk"])
Db = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def _material(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "material_pn": str(row[0]),
        "material_name": str(row[1]),
        "commodity": str(row[2]),
        "item_group": str(row[3]),
        "risk_level": str(row[4]),
        "doi_days": float(row[5]),
        "lt_coverage": float(row[6]),
        "supplier_concentration": float(row[7]),
        "gap_qty": int(row[8]),
        "gap_date": _iso(row[9]),
        "risk_reasons": str(row[10] or ""),
    }


@router.get("/summary", response_model=RiskSummary)
def risk_summary(connection: Db) -> dict[str, Any]:
    latest = connection.execute("SELECT max(eval_date) FROM material_risk").fetchone()[0]
    if latest is None:
        raise HTTPException(status_code=404, detail={"code": "risk_not_found", "message": "No risk snapshot found"})
    counts = connection.execute(
        "SELECT count(*) FILTER (WHERE risk_level = 'RED'), "
        "count(*) FILTER (WHERE risk_level = 'ORANGE'), "
        "count(*) FILTER (WHERE risk_level = 'YELLOW'), "
        "count(*) FILTER (WHERE risk_level = 'GREEN'), coalesce(sum(gap_qty), 0), count(*) "
        "FROM material_risk WHERE eval_date = ?",
        [latest],
    ).fetchone()
    red, orange, yellow, green, gap, total = (int(value) for value in counts)
    commodity_rows = connection.execute(
        "SELECT commodity, red_count, orange_count, yellow_count, green_count, total_gap_qty "
        "FROM v_risk_by_commodity WHERE eval_date = ? ORDER BY commodity",
        [latest],
    ).fetchall()
    supplier_rows = connection.execute(
        "SELECT supplier_id, supplier_name, red_orange_material_count, weighted_gap_qty "
        "FROM v_risk_by_supplier WHERE eval_date = ? "
        "ORDER BY weighted_gap_qty DESC, supplier_id LIMIT 5",
        [latest],
    ).fetchall()
    return {
        "eval_date": latest.isoformat(),
        "red_count": red,
        "orange_count": orange,
        "yellow_count": yellow,
        "green_count": green,
        "total_gap_qty": gap,
        "red_orange_pct": round((red + orange) * 100 / total, 2) if total else 0.0,
        "by_commodity": [
            {
                "commodity": str(row[0]),
                "red_count": int(row[1]),
                "orange_count": int(row[2]),
                "yellow_count": int(row[3]),
                "green_count": int(row[4]),
                "total_gap_qty": int(row[5]),
            }
            for row in commodity_rows
        ],
        "top_suppliers": [
            {
                "supplier_id": str(row[0]),
                "supplier_name": str(row[1]),
                "red_orange_material_count": int(row[2]),
                "weighted_gap_qty": float(row[3]),
            }
            for row in supplier_rows
        ],
    }


@router.get("/materials", response_model=list[MaterialRisk])
def risk_materials(
    connection: Db,
    level: str | None = None,
    commodity: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    clauses = ["r.eval_date = (SELECT max(eval_date) FROM material_risk)"]
    parameters: list[Any] = []
    if level is not None:
        clauses.append("r.risk_level = ?")
        parameters.append(level)
    if commodity is not None:
        clauses.append("m.commodity = ?")
        parameters.append(commodity)
    if search is not None:
        clauses.append("(m.material_pn ILIKE ? OR m.material_name ILIKE ?)")
        pattern = f"%{search}%"
        parameters.extend([pattern, pattern])
    parameters.append(limit)
    rows = connection.execute(
        "SELECT r.material_pn, m.material_name, m.commodity, m.item_group, r.risk_level, "
        "r.doi_days, r.lt_coverage, r.supplier_concentration, r.gap_qty, r.gap_date, "
        "r.risk_reasons FROM material_risk r JOIN materials m USING (material_pn) WHERE "
        + " AND ".join(clauses)
        + " ORDER BY CASE r.risk_level WHEN 'RED' THEN 1 WHEN 'ORANGE' THEN 2 "
        "WHEN 'YELLOW' THEN 3 ELSE 4 END, r.gap_qty DESC, r.material_pn LIMIT ?",
        parameters,
    ).fetchall()
    return [_material(row) for row in rows]


@router.get("/materials/{material_pn}", response_model=MaterialRiskDetail)
def risk_material_detail(material_pn: str, connection: Db) -> dict[str, Any]:
    row = connection.execute(
        "WITH profile AS (SELECT material_pn, min(lead_time_days) AS min_lt, "
        "arg_max(supplier_id, split_pct) AS primary_supplier_id FROM supply_split "
        "GROUP BY material_pn) SELECT r.material_pn, m.material_name, m.commodity, "
        "m.item_group, r.risk_level, r.doi_days, r.lt_coverage, r.supplier_concentration, "
        "r.gap_qty, r.gap_date, r.risk_reasons, p.min_lt, p.primary_supplier_id "
        "FROM material_risk r JOIN materials m USING (material_pn) "
        "LEFT JOIN profile p USING (material_pn) WHERE r.material_pn = ? "
        "AND r.eval_date = (SELECT max(eval_date) FROM material_risk)",
        [material_pn],
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "material_not_found", "message": f"Material {material_pn} not found"},
        )
    base = _material(row)
    contributors = top_sku_contributors(connection, material_pn)
    explanation_row = {**base, "min_lt": int(row[11] or 0), "primary_supplier_id": row[12]}
    supplier_rows = connection.execute(
        "SELECT ss.supplier_id, s.supplier_name, ss.split_pct, ss.lead_time_days, ss.moq "
        "FROM supply_split ss JOIN suppliers s USING (supplier_id) WHERE material_pn = ? "
        "ORDER BY ss.split_pct DESC, ss.supplier_id",
        [material_pn],
    ).fetchall()
    po_rows = connection.execute(
        "SELECT po.po_id, po.supplier_id, s.supplier_name, po.qty, po.eta_date "
        "FROM open_po po JOIN suppliers s USING (supplier_id) WHERE po.material_pn = ? "
        "AND po.eta_date > (SELECT max(date) FROM sales_daily) ORDER BY po.eta_date, po.po_id",
        [material_pn],
    ).fetchall()
    return {
        **base,
        "explanation": explain_risk(explanation_row, contributors),
        "top_skus": contributors,
        "suppliers": [
            {
                "supplier_id": str(item[0]),
                "supplier_name": str(item[1]),
                "split_pct": float(item[2]),
                "lead_time_days": int(item[3]),
                "moq": int(item[4]),
            }
            for item in supplier_rows
        ],
        "open_pos": [
            {
                "po_id": str(item[0]),
                "supplier_id": str(item[1]),
                "supplier_name": str(item[2]),
                "qty": int(item[3]),
                "eta_date": item[4].isoformat(),
            }
            for item in po_rows
        ],
    }
