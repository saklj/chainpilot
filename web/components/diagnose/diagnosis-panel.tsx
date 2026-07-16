"use client";

import { AlertTriangle, CheckCircle2, LoaderCircle, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { streamDiagnosis } from "@/lib/diagnose-stream";
import type { DiagnosisResultEvent, DiagnosisStepEvent } from "@/lib/schemas";

const categoryNames = {
  single_source_supply: "单源断供",
  shared_demand_competition: "共用料竞争",
  long_leadtime_no_po: "长交期追料不及",
  forecast_miss: "预测偏差",
  unknown: "未定位",
} as const;

export function DiagnosisPanel({ materialPn }: { materialPn: string }) {
  const [steps, setSteps] = useState<DiagnosisStepEvent[]>([]);
  const [result, setResult] = useState<DiagnosisResultEvent | null>(null);
  const [retryIndex, setRetryIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [steps, retryIndex, result, error]);

  async function start() {
    if (running) return;
    setSteps([]);
    setResult(null);
    setRetryIndex(null);
    setError(null);
    setRunning(true);
    try {
      await streamDiagnosis(materialPn, (event) => {
        if (event.type === "step") setSteps((current) => [...current, event]);
        if (event.type === "retry") setRetryIndex(event.index);
        if (event.type === "result") setResult(event);
        if (event.type === "error") setError(event.message);
      });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "诊断失败");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="space-y-3 rounded-lg border border-border bg-background p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">AI 根因诊断</h3>
          <p className="text-xs text-muted-foreground">只读工具逐步核查风险、在途和需求证据</p>
        </div>
        <Button size="sm" onClick={() => void start()} disabled={running}>
          {running ? <LoaderCircle className="animate-spin" /> : <Sparkles />}
          {running ? "诊断中…" : result || error ? "重新诊断" : "AI 诊断"}
        </Button>
      </div>

      {steps.length > 0 || retryIndex !== null ? (
        <div className="max-h-64 space-y-2 overflow-y-auto rounded-md border border-border p-3">
          {steps.map((step) => (
            <div key={`${step.index}-${step.action}`} className="space-y-1 text-xs">
              <p><span className="text-muted-foreground">步骤 {step.index}</span>{" "}<span className="font-mono text-primary">{step.action}</span></p>
              <p className="whitespace-pre-wrap break-words leading-5 text-muted-foreground">{step.observation}</p>
            </div>
          ))}
          {retryIndex !== null ? <p className="text-xs text-risk-yellow">步骤 {retryIndex} 输出格式异常，正在重试…</p> : null}
          <div ref={bottomRef} />
        </div>
      ) : null}

      {result ? (
        <div className="space-y-3 rounded-md border border-primary/30 bg-primary/5 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="border-primary/40 bg-primary/10 text-primary">{categoryNames[result.category]}</Badge>
            {result.guardrail === "pass" ? <span className="flex items-center gap-1 text-xs text-risk-green"><CheckCircle2 className="size-3.5" />证据护栏通过</span> : null}
          </div>
          <p className="whitespace-pre-wrap break-words text-sm leading-6">{result.root_cause}</p>
          {result.degraded ? <p className="rounded border border-risk-yellow/30 bg-risk-yellow/10 px-3 py-2 text-xs text-risk-yellow">结论已降级为纯证据模板</p> : null}
        </div>
      ) : null}

      {error ? <p className="flex items-center gap-2 rounded-md border border-destructive/30 p-3 text-xs text-destructive"><AlertTriangle className="size-4" />诊断失败：{error}</p> : null}
    </section>
  );
}
