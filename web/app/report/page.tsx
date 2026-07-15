import { AlertTriangle } from "lucide-react";

import { ReportWorkspace } from "@/components/report/report-workspace";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getLatestReport, getReportList, getRiskSummary } from "@/lib/api";
import type { Report, ReportMeta, RiskSummary } from "@/lib/schemas";

type ReportPageState =
  | {
      status: "ready";
      report: Report;
      history: ReportMeta[];
      summary: RiskSummary | null;
    }
  | { status: "error"; message: string };

async function loadReportPage(): Promise<ReportPageState> {
  try {
    const [report, history] = await Promise.all([getLatestReport(), getReportList()]);
    const summary = await getRiskSummary().catch(() => null);
    return { status: "ready", report, history, summary };
  } catch (error) {
    return {
      status: "error",
      message: error instanceof Error ? error.message : "未知错误",
    };
  }
}

export default async function ReportPage() {
  const state = await loadReportPage();

  if (state.status === "error") {
    return (
      <section className="mx-auto w-full max-w-7xl">
        <Card className="border border-border bg-card ring-0">
          <CardHeader>
            <div className="flex items-center gap-3">
              <AlertTriangle className="size-5 text-muted-foreground" aria-hidden="true" />
              <CardTitle>暂时无法加载供应风险周报</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>请确认 ChainPilot API 已启动且已有可用周报，然后刷新页面。</p>
            <p className="font-mono text-xs text-foreground/70">{state.message}</p>
          </CardContent>
        </Card>
      </section>
    );
  }

  return (
    <ReportWorkspace
      initialReport={state.report}
      history={state.history}
      riskSummary={state.summary}
    />
  );
}
