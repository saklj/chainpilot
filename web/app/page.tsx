import { AlertTriangle } from "lucide-react";

import { ForecastChart } from "@/components/dashboard/forecast-chart";
import { KpiCards } from "@/components/dashboard/kpi-cards";
import { RiskTable } from "@/components/dashboard/risk-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getForecastMetrics, getRiskMaterials, getRiskSummary, getSkus } from "@/lib/api";
import type { ForecastMetric, MaterialRisk, RiskSummary, SkuInfo } from "@/lib/schemas";

type DashboardData = {
  summary: RiskSummary;
  forecastMetrics: ForecastMetric[];
  materials: MaterialRisk[];
  skus: SkuInfo[];
};

type DashboardState =
  | { status: "ready"; data: DashboardData }
  | { status: "error"; message: string };

async function loadDashboard(): Promise<DashboardState> {
  try {
    const [summary, forecastMetrics, materials, skus] = await Promise.all([
      getRiskSummary(),
      getForecastMetrics(),
      getRiskMaterials({ limit: 300 }),
      getSkus(),
    ]);
    return { status: "ready", data: { summary, forecastMetrics, materials, skus } };
  } catch (error: unknown) {
    return {
      status: "error",
      message: error instanceof Error ? error.message : "未知错误",
    };
  }
}

function averageMetric(
  metrics: ForecastMetric[],
  modelName: string,
  field: "mape" | "wmape" | "wrmsse",
): number {
  const selected = metrics.filter((metric) => metric.model_name === modelName);
  return selected.reduce((sum, metric) => sum + metric[field], 0) / selected.length;
}

export default async function DashboardPage() {
  const state = await loadDashboard();

  if (state.status === "error") {
    return (
      <section className="mx-auto w-full max-w-7xl">
        <Card className="border border-border bg-card ring-0">
          <CardHeader>
            <div className="flex items-center gap-3">
              <AlertTriangle className="size-5 text-muted-foreground" aria-hidden="true" />
              <CardTitle>暂时无法加载风险数据</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>请确认 ChainPilot API 已在本地启动，然后刷新页面。</p>
            <p className="font-mono text-xs text-foreground/70">{state.message}</p>
          </CardContent>
        </Card>
      </section>
    );
  }

  const lightgbmMape = averageMetric(state.data.forecastMetrics, "lightgbm", "mape");
  const baselineMape = averageMetric(state.data.forecastMetrics, "seasonal_naive", "mape");
  const mapeImprovement = ((baselineMape - lightgbmMape) / baselineMape) * 100;
  const lightgbmWmape = averageMetric(state.data.forecastMetrics, "lightgbm", "wmape");
  const baselineWmape = averageMetric(state.data.forecastMetrics, "seasonal_naive", "wmape");
  const wmapeImprovement = ((baselineWmape - lightgbmWmape) / baselineWmape) * 100;
  const lightgbmWrmsse = averageMetric(state.data.forecastMetrics, "lightgbm", "wrmsse");
  const counts = {
    RED: state.data.summary.red_count,
    ORANGE: state.data.summary.orange_count,
    YELLOW: state.data.summary.yellow_count,
    GREEN: state.data.summary.green_count,
  };

  return (
    <section className="mx-auto w-full max-w-7xl space-y-6">
      <div className="space-y-2">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          评估日期 {state.data.summary.eval_date}
        </p>
        <h1 className="text-[length:var(--font-headline-size)] font-semibold tracking-tight">
          供应风险概览
        </h1>
        <p className="text-sm text-muted-foreground">从风险概览发现问题，逐层钻取至物料与 SKU。</p>
      </div>

      <KpiCards
        redCount={state.data.summary.red_count}
        orangeCount={state.data.summary.orange_count}
        totalGapQty={state.data.summary.total_gap_qty}
        redOrangePct={state.data.summary.red_orange_pct}
        forecastAccuracy={100 - lightgbmWmape}
        wmapeImprovement={wmapeImprovement}
        lightgbmMape={lightgbmMape}
        mapeImprovement={mapeImprovement}
        lightgbmWrmsse={lightgbmWrmsse}
      />
      <RiskTable initialMaterials={state.data.materials} counts={counts} />
      <ForecastChart skus={state.data.skus} />
    </section>
  );
}
