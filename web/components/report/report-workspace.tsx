"use client";

import { AlertTriangle, CalendarDays, Download, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { ReportCharts } from "@/components/report/report-charts";
import { ReportMarkdown } from "@/components/report/report-markdown";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getReport } from "@/lib/api";
import type { Report, ReportMeta, RiskSummary } from "@/lib/schemas";
import { cn } from "@/lib/utils";

function formatCreatedAt(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Australia/Sydney",
  }).format(new Date(value));
}

export function ReportWorkspace({
  initialReport,
  history,
  riskSummary,
}: {
  initialReport: Report;
  history: ReportMeta[];
  riskSummary: RiskSummary | null;
}) {
  const [report, setReport] = useState(initialReport);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const showCharts = riskSummary?.eval_date === report.report_date;

  async function selectReport(reportDate: string) {
    if (reportDate === report.report_date || loading) return;
    setLoading(true);
    setError(null);
    try {
      setReport(await getReport(reportDate));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "周报加载失败");
    } finally {
      setLoading(false);
    }
  }

  function exportMarkdown() {
    const blob = new Blob([report.content_md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `chainpilot-weekly-${report.report_date}.md`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="mx-auto w-full max-w-7xl space-y-5">
      <header className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-2">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">M5 · T5</p>
          <h1 className="text-[length:var(--font-headline-size)] font-semibold tracking-tight">
            供应风险周报
          </h1>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <CalendarDays className="size-3.5" aria-hidden="true" /> 报告日期 {report.report_date}
            </span>
            <span>生成于 {formatCreatedAt(report.created_at)}</span>
          </div>
        </div>
        <Button variant="outline" onClick={exportMarkdown}>
          <Download aria-hidden="true" /> 导出 Markdown
        </Button>
      </header>

      <div className="xl:hidden">
        <Select value={report.report_date} onValueChange={(value) => void selectReport(value)}>
          <SelectTrigger className="w-full" aria-label="选择历史周报">
            <SelectValue placeholder="选择历史周报" />
          </SelectTrigger>
          <SelectContent>
            {history.map((item) => (
              <SelectItem key={item.report_date} value={item.report_date}>
                {item.report_date}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {error ? (
        <Card className="border border-destructive/30 bg-card ring-0">
          <CardContent className="flex items-center gap-3 text-sm text-muted-foreground">
            <AlertTriangle className="size-4 shrink-0 text-destructive" aria-hidden="true" />
            历史周报加载失败：{error}。当前报告仍可继续查看。
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_240px]">
        <main className={cn("min-w-0 space-y-5 transition-opacity", loading && "opacity-60")}>
          {loading ? (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" /> 正在切换周报…
            </p>
          ) : null}

          {report.narrative_fallbacks.length > 0 ? (
            <div className="rounded-lg border border-primary/30 bg-primary/10 px-4 py-3 text-sm text-foreground/90">
              以下叙述段由确定性模板兜底生成：{report.narrative_fallbacks.join("、")}
            </div>
          ) : null}

          {showCharts && riskSummary ? <ReportCharts summary={riskSummary} /> : null}

          <Card className="border border-border bg-card ring-0">
            <CardContent className="px-4 py-2 sm:px-6">
              <ReportMarkdown content={report.content_md} />
            </CardContent>
          </Card>
        </main>

        <aside className="hidden xl:block">
          <div className="sticky top-20 rounded-xl border border-border bg-card p-3">
            <p className="px-2 pb-3 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              历史周报
            </p>
            <nav className="space-y-1" aria-label="历史周报">
              {history.map((item) => {
                const active = item.report_date === report.report_date;
                return (
                  <button
                    key={item.report_date}
                    type="button"
                    disabled={loading}
                    onClick={() => void selectReport(item.report_date)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "w-full rounded-lg px-3 py-2.5 text-left transition-colors disabled:opacity-50",
                      active
                        ? "bg-primary/15 text-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <span className="block text-sm font-medium">{item.report_date}</span>
                    <span className="mt-0.5 block text-[11px]">{formatCreatedAt(item.created_at)}</span>
                  </button>
                );
              })}
            </nav>
          </div>
        </aside>
      </div>
    </section>
  );
}
