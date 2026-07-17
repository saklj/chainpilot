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


class ForecastMetric(BaseModel):
    model_name: str
    fold: int
    mape: float
    wmape: float
    wrmsse: float


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


class WhatIfSupplier(BaseModel):
    supplier_id: str
    supplier_name: str
    red_orange_material_count: int
    weighted_gap_qty: float


class WhatIfRequest(BaseModel):
    supplier_id: str
    # Capped at the 28-day forecast horizon: beyond it the engine cannot see additional
    # demand, so longer outages would silently return identical (understated) results.
    days: int = Field(default=14, ge=1, le=28)


class WhatIfSummary(BaseModel):
    baseline_red_count: int
    baseline_orange_count: int
    new_red_count: int
    new_orange_count: int
    total_gap_delta: int
    affected_sku_count: int
    exposure_amount: float


class WhatIfMaterial(BaseModel):
    material_pn: str
    baseline_level: Literal["RED", "ORANGE", "YELLOW", "GREEN"]
    scenario_level: Literal["RED", "ORANGE", "YELLOW", "GREEN"]
    baseline_gap: int
    scenario_gap: int
    gap_delta: int
    split_pct: float


class WhatIfSku(BaseModel):
    sku_id: str
    affected_units: float
    unit_price: float
    exposure_amount: float


class WhatIfResponse(BaseModel):
    summary: WhatIfSummary
    worsened_materials: list[WhatIfMaterial]
    affected_skus: list[WhatIfSku]


class DiagnosisRequest(BaseModel):
    material_pn: str = Field(min_length=1, max_length=50)


class IngestTemplatePreview(BaseModel):
    source_columns: list[str]
    suggested_mapping: dict[str, str | None]
    suggestion_sources: dict[str, Literal["deterministic", "llm"] | None]


class IngestTemplateSaveRequest(BaseModel):
    mapping: dict[str, str]


class IngestTemplateState(BaseModel):
    exists: bool
    target_table: Literal["open_po"] = "open_po"
    mapping: dict[str, str] | None = None
    created_at: str | None = None


class IngestValidatedRow(BaseModel):
    po_id: str
    material_pn: str
    supplier_id: str
    qty: int
    eta_date: str


class IngestValidationError(BaseModel):
    row: int
    field: str
    code: str
    reason: str


class IngestValidationReport(BaseModel):
    validation_token: str
    filename: str
    total_rows: int
    valid_count: int
    error_count: int
    errors: list[IngestValidationError]
    preview: list[IngestValidatedRow]


class IngestConfirmRequest(BaseModel):
    validation_token: str = Field(min_length=1, max_length=200)


class IngestImportResult(BaseModel):
    batch_id: str
    row_count: int


class IngestRollbackRequest(BaseModel):
    batch_id: str = Field(min_length=1, max_length=100)


class IngestRollbackResult(BaseModel):
    batch_id: str
    deleted_count: int


class IngestBatch(BaseModel):
    batch_id: str
    filename: str
    row_count: int
    created_at: str
