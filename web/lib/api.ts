import { z } from "zod";

import {
  ChatResultSchema,
  ForecastMetricSchema,
  IngestBatchSchema,
  IngestImportResultSchema,
  IngestMailConfigSchema,
  IngestMailConfirmResultSchema,
  IngestMailItemDetailSchema,
  IngestMailItemSchema,
  IngestMailPollResultSchema,
  IngestMailRejectResultSchema,
  IngestRollbackResultSchema,
  IngestTemplatePreviewSchema,
  IngestTemplateStateSchema,
  IngestValidationReportSchema,
  MaterialRiskDetailSchema,
  MaterialRiskSchema,
  ReportMetaSchema,
  ReportSchema,
  RiskSummarySchema,
  SkuForecastSchema,
  SkuInfoSchema,
  WhatIfResultSchema,
  WhatIfSupplierSchema,
  type ChatResult,
  type ForecastMetric,
  type IngestBatch,
  type IngestImportResult,
  type IngestMailConfig,
  type IngestMailConfirmResult,
  type IngestMailItem,
  type IngestMailItemDetail,
  type IngestMailPollResult,
  type IngestRollbackResult,
  type IngestTemplatePreview,
  type IngestTemplateState,
  type IngestValidationReport,
  type MaterialRisk,
  type MaterialRiskDetail,
  type Report,
  type ReportMeta,
  type RiskSummary,
  type SkuForecast,
  type SkuInfo,
  type WhatIfResult,
  type WhatIfSupplier,
} from "@/lib/schemas";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown,
  ) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  if (!isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers,
  });

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? payload.detail
        : payload;
    throw new ApiError(response.status, detail);
  }
  return schema.parse(payload);
}

export function getRiskSummary(): Promise<RiskSummary> {
  return request("/api/risk/summary", RiskSummarySchema);
}

export type RiskMaterialParams = {
  level?: "RED" | "ORANGE" | "YELLOW" | "GREEN";
  commodity?: string;
  search?: string;
  limit?: number;
};

export function getRiskMaterials(params: RiskMaterialParams = {}): Promise<MaterialRisk[]> {
  const searchParams = new URLSearchParams();
  if (params.level !== undefined) searchParams.set("level", params.level);
  if (params.commodity !== undefined) searchParams.set("commodity", params.commodity);
  if (params.search !== undefined) searchParams.set("search", params.search);
  if (params.limit !== undefined) searchParams.set("limit", String(params.limit));
  const query = searchParams.size > 0 ? `?${searchParams.toString()}` : "";
  return request(`/api/risk/materials${query}`, z.array(MaterialRiskSchema));
}

export function getRiskMaterialDetail(materialPn: string): Promise<MaterialRiskDetail> {
  return request(`/api/risk/materials/${encodeURIComponent(materialPn)}`, MaterialRiskDetailSchema);
}

export function getSkus(): Promise<SkuInfo[]> {
  return request("/api/forecast/skus", z.array(SkuInfoSchema));
}

export function getForecastMetrics(): Promise<ForecastMetric[]> {
  return request("/api/forecast/metrics", z.array(ForecastMetricSchema));
}

export function getSkuForecast(skuId: string, historyDays?: number): Promise<SkuForecast> {
  const query = historyDays === undefined ? "" : `?history_days=${encodeURIComponent(historyDays)}`;
  return request(`/api/forecast/${encodeURIComponent(skuId)}${query}`, SkuForecastSchema);
}

export function postChat(question: string): Promise<ChatResult> {
  return request("/api/chat", ChatResultSchema, {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

export function getLatestReport(): Promise<Report> {
  return request("/api/report/latest", ReportSchema);
}

export function getReport(reportDate: string): Promise<Report> {
  return request(`/api/report/${encodeURIComponent(reportDate)}`, ReportSchema);
}

export async function getReportWorkbook(reportDate: string): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/api/report/${encodeURIComponent(reportDate)}/xlsx`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? payload.detail
        : payload;
    throw new ApiError(response.status, detail);
  }
  return response.blob();
}

export function getReportList(): Promise<ReportMeta[]> {
  return request("/api/report/list", z.array(ReportMetaSchema));
}

export function getWhatIfSuppliers(): Promise<WhatIfSupplier[]> {
  return request("/api/whatif/suppliers", z.array(WhatIfSupplierSchema));
}

export function simulateSupplierOutage(
  supplierId: string,
  days: number,
): Promise<WhatIfResult> {
  return request("/api/whatif/simulate", WhatIfResultSchema, {
    method: "POST",
    body: JSON.stringify({ supplier_id: supplierId, days }),
  });
}

export function getIngestTemplate(): Promise<IngestTemplateState> {
  return request("/api/ingest/template", IngestTemplateStateSchema);
}

export function previewIngestTemplate(file: File): Promise<IngestTemplatePreview> {
  const body = new FormData();
  body.set("file", file);
  return request("/api/ingest/template/preview", IngestTemplatePreviewSchema, {
    method: "POST",
    body,
  });
}

export function saveIngestTemplate(
  mapping: Record<string, string>,
): Promise<IngestTemplateState> {
  return request("/api/ingest/template", IngestTemplateStateSchema, {
    method: "POST",
    body: JSON.stringify({ mapping }),
  });
}

export function validateIngestFile(file: File): Promise<IngestValidationReport> {
  const body = new FormData();
  body.set("file", file);
  return request("/api/ingest/validate", IngestValidationReportSchema, {
    method: "POST",
    body,
  });
}

export function confirmIngest(validationToken: string): Promise<IngestImportResult> {
  return request("/api/ingest/confirm", IngestImportResultSchema, {
    method: "POST",
    body: JSON.stringify({ validation_token: validationToken }),
  });
}

export function rollbackIngest(batchId: string): Promise<IngestRollbackResult> {
  return request("/api/ingest/rollback", IngestRollbackResultSchema, {
    method: "POST",
    body: JSON.stringify({ batch_id: batchId }),
  });
}

export function getIngestBatches(): Promise<IngestBatch[]> {
  return request("/api/ingest/batches", z.array(IngestBatchSchema));
}

export function pollIngestMail(): Promise<IngestMailPollResult> {
  return request("/api/ingest/mail/poll", IngestMailPollResultSchema, { method: "POST" });
}

export function getIngestMailItems(): Promise<IngestMailItem[]> {
  return request("/api/ingest/mail/items", z.array(IngestMailItemSchema));
}

export function getIngestMailItem(itemId: string): Promise<IngestMailItemDetail> {
  return request(
    `/api/ingest/mail/items/${encodeURIComponent(itemId)}`,
    IngestMailItemDetailSchema,
  );
}

export function confirmIngestMailItem(itemId: string): Promise<IngestMailConfirmResult> {
  return request(
    `/api/ingest/mail/items/${encodeURIComponent(itemId)}/confirm`,
    IngestMailConfirmResultSchema,
    { method: "POST" },
  );
}

export function rejectIngestMailItem(itemId: string): Promise<{ item_id: string; status: "rejected" }> {
  return request(
    `/api/ingest/mail/items/${encodeURIComponent(itemId)}/reject`,
    IngestMailRejectResultSchema,
    { method: "POST" },
  );
}

export function getIngestMailConfig(): Promise<IngestMailConfig> {
  return request("/api/ingest/mail/config", IngestMailConfigSchema);
}
