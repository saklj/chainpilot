"""Read-only supplier-outage what-if endpoints."""

from dataclasses import asdict
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException

from analytics.whatif import simulate_supplier_outage
from app.deps import get_db
from app.schemas import WhatIfRequest, WhatIfResponse, WhatIfSupplier

router = APIRouter(prefix="/api/whatif", tags=["what-if"])
Db = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]


@router.get("/suppliers", response_model=list[WhatIfSupplier])
def suppliers(connection: Db) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT supplier_id, supplier_name, red_orange_material_count, weighted_gap_qty "
        "FROM v_risk_by_supplier "
        "WHERE eval_date = (SELECT max(eval_date) FROM material_risk) "
        "ORDER BY weighted_gap_qty DESC, supplier_id"
    ).fetchall()
    return [
        {
            "supplier_id": str(row[0]),
            "supplier_name": str(row[1]),
            "red_orange_material_count": int(row[2]),
            "weighted_gap_qty": float(row[3]),
        }
        for row in rows
    ]


@router.post("/simulate", response_model=WhatIfResponse)
def simulate(request: WhatIfRequest, connection: Db) -> dict[str, Any]:
    try:
        result = simulate_supplier_outage(connection, request.supplier_id, request.days)
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "supplier_not_found",
                "message": f"Supplier {request.supplier_id} not found",
            },
        ) from error
    return {
        "summary": asdict(result.summary),
        "worsened_materials": [asdict(row) for row in result.worsened_materials],
        "affected_skus": [asdict(row) for row in result.affected_skus],
    }
