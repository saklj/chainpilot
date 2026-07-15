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
