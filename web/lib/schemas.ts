import { z } from "zod";

export const HealthResponseSchema = z.object({
  status: z.literal("ok"),
  service: z.literal("chainpilot-api"),
  version: z.string(),
});
export type HealthResponse = z.infer<typeof HealthResponseSchema>;

export const CommodityRiskSchema = z.object({
  commodity: z.string(),
  red_count: z.number().int(),
  orange_count: z.number().int(),
  yellow_count: z.number().int(),
  green_count: z.number().int(),
  total_gap_qty: z.number().int(),
});
export type CommodityRisk = z.infer<typeof CommodityRiskSchema>;

export const SupplierExposureSchema = z.object({
  supplier_id: z.string(),
  supplier_name: z.string(),
  red_orange_material_count: z.number().int(),
  weighted_gap_qty: z.number(),
});
export type SupplierExposure = z.infer<typeof SupplierExposureSchema>;

export const RiskSummarySchema = z.object({
  eval_date: z.string(),
  red_count: z.number().int(),
  orange_count: z.number().int(),
  yellow_count: z.number().int(),
  green_count: z.number().int(),
  total_gap_qty: z.number().int(),
  red_orange_pct: z.number(),
  by_commodity: z.array(CommodityRiskSchema),
  top_suppliers: z.array(SupplierExposureSchema),
});
export type RiskSummary = z.infer<typeof RiskSummarySchema>;

export const RiskLevelSchema = z.enum(["RED", "ORANGE", "YELLOW", "GREEN"]);

export const MaterialRiskSchema = z.object({
  material_pn: z.string(),
  material_name: z.string(),
  commodity: z.string(),
  item_group: z.string(),
  risk_level: RiskLevelSchema,
  doi_days: z.number(),
  lt_coverage: z.number(),
  supplier_concentration: z.number(),
  gap_qty: z.number().int(),
  gap_date: z.string().nullable(),
  risk_reasons: z.string(),
});
export type MaterialRisk = z.infer<typeof MaterialRiskSchema>;

export const SkuContributionSchema = z.object({
  sku_id: z.string(),
  demand_qty: z.number(),
});
export type SkuContribution = z.infer<typeof SkuContributionSchema>;

export const SupplySplitRowSchema = z.object({
  supplier_id: z.string(),
  supplier_name: z.string(),
  split_pct: z.number(),
  lead_time_days: z.number().int(),
  moq: z.number().int(),
});
export type SupplySplitRow = z.infer<typeof SupplySplitRowSchema>;

export const OpenPOSchema = z.object({
  po_id: z.string(),
  supplier_id: z.string(),
  supplier_name: z.string(),
  qty: z.number().int(),
  eta_date: z.string(),
});
export type OpenPO = z.infer<typeof OpenPOSchema>;

export const MaterialRiskDetailSchema = MaterialRiskSchema.extend({
  explanation: z.string(),
  top_skus: z.array(SkuContributionSchema),
  suppliers: z.array(SupplySplitRowSchema),
  open_pos: z.array(OpenPOSchema),
});
export type MaterialRiskDetail = z.infer<typeof MaterialRiskDetailSchema>;

export const SkuInfoSchema = z.object({
  sku_id: z.string(),
  product_name: z.string(),
  product_family: z.string(),
});
export type SkuInfo = z.infer<typeof SkuInfoSchema>;

export const HistoryPointSchema = z.object({
  date: z.string(),
  units_sold: z.number().int(),
});
export type HistoryPoint = z.infer<typeof HistoryPointSchema>;

export const ForecastPointSchema = z.object({
  date: z.string(),
  model_name: z.string(),
  yhat: z.number(),
});
export type ForecastPoint = z.infer<typeof ForecastPointSchema>;

export const ForecastMetricSchema = z.object({
  model_name: z.string(),
  fold: z.number().int(),
  mape: z.number(),
  wmape: z.number(),
  wrmsse: z.number(),
});
export type ForecastMetric = z.infer<typeof ForecastMetricSchema>;

export const SkuForecastSchema = z.object({
  sku_id: z.string(),
  product_name: z.string(),
  history: z.array(HistoryPointSchema),
  forecast: z.array(ForecastPointSchema),
});
export type SkuForecast = z.infer<typeof SkuForecastSchema>;

export const ChatRequestSchema = z.object({
  question: z.string().min(1).max(500),
});
export type ChatRequest = z.infer<typeof ChatRequestSchema>;

export const VerdictMatchSchema = z.object({
  value: z.string(),
  row: z.number().int(),
  column: z.number().int(),
});
export type VerdictMatch = z.infer<typeof VerdictMatchSchema>;

export const VerdictSchema = z.object({
  verdict: z.enum(["pass", "fail"]),
  matched: z.array(VerdictMatchSchema),
  unmatched: z.array(z.string()),
  checked_count: z.number().int(),
});
export type Verdict = z.infer<typeof VerdictSchema>;

export const UsageSchema = z.object({
  prompt_tokens: z.number().int(),
  completion_tokens: z.number().int(),
});
export type Usage = z.infer<typeof UsageSchema>;

export const ChatResultSchema = z.object({
  question: z.string(),
  answer: z.string(),
  refused: z.boolean(),
  refusal_reason: z.string().nullable(),
  sql: z.string().nullable(),
  final_sql: z.string().nullable(),
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  row_count: z.number().int(),
  verdict: VerdictSchema.nullable(),
  draft_answer: z.string().nullable(),
  usage: UsageSchema,
});
export type ChatResult = z.infer<typeof ChatResultSchema>;

export const ChatStageEventSchema = z.object({
  type: z.literal("stage"),
  stage: z.literal("generating_sql"),
});

export const ChatSqlEventSchema = z.object({
  type: z.literal("sql"),
  sql: z.string(),
});

export const ChatRowsEventSchema = z.object({
  type: z.literal("rows"),
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  row_count: z.number().int(),
});

export const ChatAnswerEventSchema = z.object({
  type: z.literal("answer"),
  answer: z.string(),
});

export const ChatResultEventSchema = z.object({
  type: z.literal("result"),
  result: ChatResultSchema,
});

export const ChatEventSchema = z.discriminatedUnion("type", [
  ChatStageEventSchema,
  ChatSqlEventSchema,
  ChatRowsEventSchema,
  ChatAnswerEventSchema,
  ChatResultEventSchema,
]);
export type ChatEvent = z.infer<typeof ChatEventSchema>;

export const ReportSchema = z.object({
  report_date: z.string(),
  content_md: z.string(),
  narrative_fallbacks: z.array(z.string()),
  created_at: z.string(),
});
export type Report = z.infer<typeof ReportSchema>;

export const ReportMetaSchema = z.object({
  report_date: z.string(),
  created_at: z.string(),
});
export type ReportMeta = z.infer<typeof ReportMetaSchema>;

export const WhatIfSupplierSchema = z.object({
  supplier_id: z.string(),
  supplier_name: z.string(),
  red_orange_material_count: z.number().int(),
  weighted_gap_qty: z.number(),
});
export type WhatIfSupplier = z.infer<typeof WhatIfSupplierSchema>;

export const WhatIfSummarySchema = z.object({
  baseline_red_count: z.number().int(),
  baseline_orange_count: z.number().int(),
  new_red_count: z.number().int(),
  new_orange_count: z.number().int(),
  total_gap_delta: z.number().int(),
  affected_sku_count: z.number().int(),
  exposure_amount: z.number(),
});

export const WhatIfMaterialSchema = z.object({
  material_pn: z.string(),
  baseline_level: RiskLevelSchema,
  scenario_level: RiskLevelSchema,
  baseline_gap: z.number().int(),
  scenario_gap: z.number().int(),
  gap_delta: z.number().int(),
  split_pct: z.number(),
});

export const WhatIfSkuSchema = z.object({
  sku_id: z.string(),
  affected_units: z.number(),
  unit_price: z.number(),
  exposure_amount: z.number(),
});

export const WhatIfResultSchema = z.object({
  summary: WhatIfSummarySchema,
  worsened_materials: z.array(WhatIfMaterialSchema),
  affected_skus: z.array(WhatIfSkuSchema),
});
export type WhatIfResult = z.infer<typeof WhatIfResultSchema>;

export const DiagnosisCategorySchema = z.enum([
  "single_source_supply",
  "shared_demand_competition",
  "long_leadtime_no_po",
  "forecast_miss",
  "unknown",
]);

export const DiagnosisStepEventSchema = z.object({
  type: z.literal("step"),
  index: z.number().int(),
  action: z.string(),
  args: z.record(z.string(), z.unknown()),
  observation: z.string(),
});
export const DiagnosisRetryEventSchema = z.object({
  type: z.literal("retry"),
  index: z.number().int(),
});
export const DiagnosisResultEventSchema = z.object({
  type: z.literal("result"),
  category: DiagnosisCategorySchema,
  root_cause: z.string(),
  steps: z.number().int(),
  degraded: z.boolean(),
  guardrail: z.string(),
});
export const DiagnosisErrorEventSchema = z.object({
  type: z.literal("error"),
  message: z.string(),
});
export const DiagnosisEventSchema = z.discriminatedUnion("type", [
  DiagnosisStepEventSchema,
  DiagnosisRetryEventSchema,
  DiagnosisResultEventSchema,
  DiagnosisErrorEventSchema,
]);
export type DiagnosisEvent = z.infer<typeof DiagnosisEventSchema>;
export type DiagnosisStepEvent = z.infer<typeof DiagnosisStepEventSchema>;
export type DiagnosisResultEvent = z.infer<typeof DiagnosisResultEventSchema>;

export const ValidationErrorSchema = z.object({
  loc: z.array(z.union([z.string(), z.number().int()])),
  msg: z.string(),
  type: z.string(),
});
export type ValidationError = z.infer<typeof ValidationErrorSchema>;

export const HTTPValidationErrorSchema = z.object({
  detail: z.array(ValidationErrorSchema).optional(),
});
export type HTTPValidationError = z.infer<typeof HTTPValidationErrorSchema>;

export const IngestTargetColumnSchema = z.enum([
  "po_id",
  "material_pn",
  "supplier_id",
  "qty",
  "eta_date",
]);
export type IngestTargetColumn = z.infer<typeof IngestTargetColumnSchema>;

export const IngestTemplatePreviewSchema = z.object({
  source_columns: z.array(z.string()),
  suggested_mapping: z.record(IngestTargetColumnSchema, z.string().nullable()),
  suggestion_sources: z.record(
    IngestTargetColumnSchema,
    z.enum(["deterministic", "llm"]).nullable(),
  ),
});
export type IngestTemplatePreview = z.infer<typeof IngestTemplatePreviewSchema>;

export const IngestTemplateStateSchema = z.object({
  exists: z.boolean(),
  target_table: z.literal("open_po"),
  mapping: z.record(z.string(), z.string()).nullable().optional(),
  created_at: z.string().nullable().optional(),
});
export type IngestTemplateState = z.infer<typeof IngestTemplateStateSchema>;

export const IngestValidatedRowSchema = z.object({
  po_id: z.string(),
  material_pn: z.string(),
  supplier_id: z.string(),
  qty: z.number().int(),
  eta_date: z.string(),
});
export type IngestValidatedRow = z.infer<typeof IngestValidatedRowSchema>;

export const IngestValidationErrorSchema = z.object({
  row: z.number().int(),
  field: z.string(),
  code: z.string(),
  reason: z.string(),
});
export type IngestValidationError = z.infer<typeof IngestValidationErrorSchema>;

export const IngestValidationReportSchema = z.object({
  validation_token: z.string(),
  filename: z.string(),
  total_rows: z.number().int(),
  valid_count: z.number().int(),
  error_count: z.number().int(),
  errors: z.array(IngestValidationErrorSchema),
  preview: z.array(IngestValidatedRowSchema),
});
export type IngestValidationReport = z.infer<typeof IngestValidationReportSchema>;

export const IngestImportResultSchema = z.object({
  batch_id: z.string(),
  row_count: z.number().int(),
});
export type IngestImportResult = z.infer<typeof IngestImportResultSchema>;

export const IngestRollbackResultSchema = z.object({
  batch_id: z.string(),
  deleted_count: z.number().int(),
});
export type IngestRollbackResult = z.infer<typeof IngestRollbackResultSchema>;

export const IngestBatchSchema = z.object({
  batch_id: z.string(),
  filename: z.string(),
  row_count: z.number().int(),
  created_at: z.string(),
});
export type IngestBatch = z.infer<typeof IngestBatchSchema>;

export const IngestMailStatusSchema = z.enum([
  "pending_review",
  "blocked",
  "invalid_file",
  "confirmed",
  "rejected",
]);
export type IngestMailStatus = z.infer<typeof IngestMailStatusSchema>;

export const IngestMailPollResultSchema = z.object({
  new_items: z.number().int(),
  blocked: z.number().int(),
  duplicates: z.number().int(),
  invalid_files: z.number().int(),
});
export type IngestMailPollResult = z.infer<typeof IngestMailPollResultSchema>;

export const IngestMailItemSchema = z.object({
  item_id: z.string(),
  message_uid: z.string(),
  sender: z.string(),
  subject: z.string(),
  filename: z.string(),
  attachment_sha256: z.string(),
  received_at: z.string(),
  status: IngestMailStatusSchema,
  valid_count: z.number().int(),
  error_count: z.number().int(),
  batch_id: z.string().nullable(),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  created_at: z.string(),
});
export type IngestMailItem = z.infer<typeof IngestMailItemSchema>;

export const IngestValidationSnapshotSchema = z.object({
  filename: z.string(),
  total_rows: z.number().int(),
  valid_count: z.number().int(),
  error_count: z.number().int(),
  errors_truncated: z.boolean(),
  errors: z.array(IngestValidationErrorSchema),
  preview: z.array(IngestValidatedRowSchema),
});
export type IngestValidationSnapshot = z.infer<typeof IngestValidationSnapshotSchema>;

export const IngestMailItemDetailSchema = IngestMailItemSchema.extend({
  fresh_report: IngestValidationSnapshotSchema.nullable(),
});
export type IngestMailItemDetail = z.infer<typeof IngestMailItemDetailSchema>;

export const IngestMailConfirmResultSchema = z.object({
  batch_id: z.string(),
  row_count: z.number().int(),
  fresh_report: IngestValidationSnapshotSchema,
});
export type IngestMailConfirmResult = z.infer<typeof IngestMailConfirmResultSchema>;

export const IngestMailRejectResultSchema = z.object({
  item_id: z.string(),
  status: z.literal("rejected"),
});

export const IngestMailConfigSchema = z.object({
  source: z.enum(["imap", "directory"]),
  scheduled_poll_enabled: z.boolean(),
  poll_seconds: z.number().int(),
  allowed_senders_configured: z.boolean(),
});
export type IngestMailConfig = z.infer<typeof IngestMailConfigSchema>;
