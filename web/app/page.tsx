import { AlertTriangle } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getRiskSummary } from "@/lib/api";
import type { RiskSummary } from "@/lib/schemas";

const numberFormatter = new Intl.NumberFormat("zh-CN");

type SummaryState =
  | { status: "ready"; summary: RiskSummary }
  | { status: "error"; message: string };

async function loadSummary(): Promise<SummaryState> {
  try {
    const summary = await getRiskSummary();
    return { status: "ready", summary };
  } catch (error: unknown) {
    return {
      status: "error",
      message: error instanceof Error ? error.message : "未知错误",
    };
  }
}

export default async function DashboardPage() {
  const state = await loadSummary();

  if (state.status === "ready") {
    const metrics = [
      {
        label: "红色风险数",
        value: numberFormatter.format(state.summary.red_count),
        className: "text-risk-red",
      },
      {
        label: "橙色风险数",
        value: numberFormatter.format(state.summary.orange_count),
        className: "text-risk-orange",
      },
      {
        label: "总缺口件数",
        value: numberFormatter.format(state.summary.total_gap_qty),
        className: "text-risk-yellow",
      },
      {
        label: "红橙占比",
        value: `${state.summary.red_orange_pct.toFixed(1)}%`,
        className: "text-risk-green",
      },
    ] as const;

    return (
      <section className="mx-auto w-full max-w-7xl space-y-6">
        <div className="space-y-2">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            评估日期 {state.summary.eval_date}
          </p>
          <h1 className="text-[length:var(--font-headline-size)] font-semibold tracking-tight">
            供应风险概览
          </h1>
          <p className="text-sm text-muted-foreground">最新物料风险快照与关键暴露指标。</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {metrics.map((metric) => (
            <Card key={metric.label} className="border border-border bg-card ring-0">
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  {metric.label}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p
                  className={`text-[length:var(--font-display-md-size)] font-semibold tracking-tight ${metric.className}`}
                >
                  {metric.value}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="mx-auto w-full max-w-7xl">
      <Card className="border border-risk-red/40 bg-card ring-0">
        <CardHeader>
          <div className="flex items-center gap-3">
            <AlertTriangle className="size-5 text-risk-red" aria-hidden="true" />
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
