"""Deterministic SOP baseline for material shortage diagnosis."""

from __future__ import annotations

import duckdb

from .diagnose import DiagnosisResult, DiagnosisTrace, get_po_status, get_risk_detail, get_shared_demand
from .llm import TokenUsage


def diagnose_material_workflow(
    connection: duckdb.DuckDBPyConnection, material_pn: str
) -> DiagnosisResult:
    detail = get_risk_detail(connection, material_pn)
    po = get_po_status(connection, material_pn)
    shared = get_shared_demand(connection, material_pn)
    values = dict(zip(detail.columns, detail.rows[0], strict=True))
    po_qty = sum(int(row[3]) for row in po.rows)
    sku_count = int(shared.rows[0][3]) if shared.rows else 0
    if int(values["supplier_count"]) == 1 and po_qty > 0:
        category = "single_source_supply"
        reason = f"单一供应源，库存 {values['qty_onhand']}，未来在途 {po_qty}，缺口 {values['gap_qty']}。"
    elif sku_count > 1:
        category = "shared_demand_competition"
        reason = f"该物料由 {sku_count} 个 SKU 共用，库存 {values['qty_onhand']}，缺口 {values['gap_qty']}。"
    elif int(values["min_lt"]) > 28 and po_qty == 0:
        category = "long_leadtime_no_po"
        reason = f"最短交期 {values['min_lt']} 天，未来在途 0，库存 {values['qty_onhand']}。"
    else:
        category = "unknown"
        reason = "现有确定性证据未命中单源、共用料或长交期零在途规则。"
    trace = [
        DiagnosisTrace("get_risk_detail", {"material_pn": material_pn}, detail.text),
        DiagnosisTrace("get_po_status", {"material_pn": material_pn}, po.text),
        DiagnosisTrace("get_shared_demand", {"material_pn": material_pn}, shared.text),
    ]
    return DiagnosisResult(category, reason, 3, trace, TokenUsage(), False, "pass")
