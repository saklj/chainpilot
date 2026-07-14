"""Read-only SKU history and forecast endpoints."""

from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_db
from app.schemas import SkuForecast, SkuInfo

router = APIRouter(prefix="/api/forecast", tags=["forecast"])
Db = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]


@router.get("/skus", response_model=list[SkuInfo])
def skus(connection: Db) -> list[dict[str, str]]:
    rows = connection.execute(
        "SELECT sku_id, product_name, product_family FROM products ORDER BY sku_id"
    ).fetchall()
    return [
        {"sku_id": str(row[0]), "product_name": str(row[1]), "product_family": str(row[2])}
        for row in rows
    ]


@router.get("/{sku_id}", response_model=SkuForecast)
def sku_forecast(
    sku_id: str,
    connection: Db,
    history_days: int = Query(default=90, ge=1, le=1095),
) -> dict[str, Any]:
    product = connection.execute(
        "SELECT product_name FROM products WHERE sku_id = ?", [sku_id]
    ).fetchone()
    if product is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "sku_not_found", "message": f"SKU {sku_id} not found"},
        )
    history = connection.execute(
        "SELECT date, units_sold FROM (SELECT date, units_sold FROM sales_daily "
        "WHERE sku_id = ? ORDER BY date DESC LIMIT ?) ORDER BY date",
        [sku_id, history_days],
    ).fetchall()
    forecast = connection.execute(
        "SELECT date, model_name, yhat FROM forecast_daily WHERE sku_id = ? "
        "ORDER BY date, model_name",
        [sku_id],
    ).fetchall()
    return {
        "sku_id": sku_id,
        "product_name": str(product[0]),
        "history": [
            {"date": row[0].isoformat(), "units_sold": int(row[1])} for row in history
        ],
        "forecast": [
            {"date": row[0].isoformat(), "model_name": str(row[1]), "yhat": float(row[2])}
            for row in forecast
        ],
    }
