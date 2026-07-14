import { z } from "zod";

import {
  ChatResultSchema,
  MaterialRiskDetailSchema,
  MaterialRiskSchema,
  ReportMetaSchema,
  ReportSchema,
  RiskSummarySchema,
  SkuForecastSchema,
  SkuInfoSchema,
  type ChatResult,
  type MaterialRisk,
  type MaterialRiskDetail,
  type Report,
  type ReportMeta,
  type RiskSummary,
  type SkuForecast,
  type SkuInfo,
} from "@/lib/schemas";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(
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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
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

export function getReportList(): Promise<ReportMeta[]> {
  return request("/api/report/list", z.array(ReportMetaSchema));
}
