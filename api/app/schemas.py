"""Pydantic response contracts mirrored by the ChainPilot frontend."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class CommodityRisk(BaseModel):
    commodity: str
    red_count: int
    orange_count: int
    yellow_count: int
    green_count: int
    total_gap_qty: int


class SupplierExposure(BaseModel):
    supplier_id: str
    supplier_name: str
    red_orange_material_count: int
    weighted_gap_qty: float


class RiskSummary(BaseModel):
    eval_date: str
    red_count: int
    orange_count: int
    yellow_count: int
    green_count: int
    total_gap_qty: int
    red_orange_pct: float
    by_commodity: list[CommodityRisk]
    top_suppliers: list[SupplierExposure]


class MaterialRisk(BaseModel):
    material_pn: str
    material_name: str
    commodity: str
    item_group: str
    risk_level: Literal["RED", "ORANGE", "YELLOW", "GREEN"]
    doi_days: float
    lt_coverage: float
    supplier_concentration: float
    gap_qty: int
    gap_date: str | None
    risk_reasons: str


class SkuContribution(BaseModel):
    sku_id: str
    demand_qty: float


class SupplySplitRow(BaseModel):
    supplier_id: str
    supplier_name: str
    split_pct: float
    lead_time_days: int
    moq: int


class OpenPO(BaseModel):
    po_id: str
    supplier_id: str
    supplier_name: str
    qty: int
    eta_date: str


class MaterialRiskDetail(MaterialRisk):
    explanation: str
    top_skus: list[SkuContribution]
    suppliers: list[SupplySplitRow]
    open_pos: list[OpenPO]


class SkuInfo(BaseModel):
    sku_id: str
    product_name: str
    product_family: str


class HistoryPoint(BaseModel):
    date: str
    units_sold: int


class ForecastPoint(BaseModel):
    date: str
    model_name: str
    yhat: float


class SkuForecast(BaseModel):
    sku_id: str
    product_name: str
    history: list[HistoryPoint]
    forecast: list[ForecastPoint]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class VerdictMatch(BaseModel):
    value: str
    row: int
    column: int


class Verdict(BaseModel):
    verdict: Literal["pass", "fail"]
    matched: list[VerdictMatch]
    unmatched: list[str]
    checked_count: int


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class ChatResult(BaseModel):
    question: str
    answer: str
    refused: bool
    refusal_reason: str | None
    sql: str | None
    final_sql: str | None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    verdict: Verdict | None
    draft_answer: str | None
    usage: Usage


class Report(BaseModel):
    report_date: str
    content_md: str
    narrative_fallbacks: list[str]
    created_at: str


class ReportMeta(BaseModel):
    report_date: str
    created_at: str
