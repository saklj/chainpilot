"use client";

import { AlertTriangle, FlaskConical, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { simulateSupplierOutage } from "@/lib/api";
import type { WhatIfResult, WhatIfSupplier } from "@/lib/schemas";

const integerFormatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const decimalFormatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const moneyFormatter = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const levelStyles = {
  RED: "border-risk-red/40 bg-risk-red/15 text-risk-red",
  ORANGE: "border-risk-orange/40 bg-risk-orange/15 text-risk-orange",
  YELLOW: "border-risk-yellow/40 bg-risk-yellow/15 text-risk-yellow",
  GREEN: "border-risk-green/40 bg-risk-green/15 text-risk-green",
} as const;

export function WhatIfWorkspace({ suppliers }: { suppliers: WhatIfSupplier[] }) {
  const [supplierId, setSupplierId] = useState(suppliers[0]?.supplier_id ?? "");
  // Keep the raw text so clearing the field never coerces to 0 (which the browser
  // would then render as a stray leading zero once the user types again).
  const [daysInput, setDaysInput] = useState("14");
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const days = Number(daysInput);
  const daysValid = Number.isInteger(days) && days >= 1 && days <= 28;
  const showDaysError = daysInput !== "" && !daysValid;

  async function runSimulation() {
    if (!supplierId || !daysValid || loading) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await simulateSupplierOutage(supplierId, days));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "模拟失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="mx-auto w-full max-w-7xl space-y-6">
      <header className="space-y-2 border-b border-border pb-5">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Scenario analysis
        </p>
        <h1 className="text-[length:var(--font-headline-size)] font-semibold tracking-tight">
          What-if 断供模拟
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          推演指定供应商在未来一段时间内断供时，物料缺口、受影响 SKU 与金额敞口的变化。
        </p>
      </header>

      <Card className="border border-border bg-card ring-0">
        <CardHeader>
          <CardTitle className="text-base">场景参数</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px_auto] md:items-end">
          <label className="space-y-2 text-sm">
            <span className="font-medium">供应商</span>
            <Select value={supplierId} onValueChange={setSupplierId}>
              <SelectTrigger className="w-full" aria-label="选择断供供应商">
                <SelectValue placeholder="选择供应商" />
              </SelectTrigger>
              <SelectContent>
                {suppliers.map((supplier) => (
                  <SelectItem key={supplier.supplier_id} value={supplier.supplier_id}>
                    {supplier.supplier_name} · 红橙 {supplier.red_orange_material_count} · 敞口{
                      integerFormatter.format(supplier.weighted_gap_qty)
                    }
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium">断供天数（1~28，上限为预测地平线）</span>
            <Input
              type="number"
              min={1}
              max={28}
              value={daysInput}
              onChange={(event) => setDaysInput(event.target.value)}
              aria-invalid={showDaysError}
            />
            {showDaysError ? (
              <p className="text-xs text-destructive">
                请输入 1~28 的整数：28 天是预测地平线上限，更长的断供无法推演。
              </p>
            ) : null}
          </label>
          <Button
            onClick={() => void runSimulation()}
            disabled={!supplierId || !daysValid || loading}
          >
            {loading ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <FlaskConical />}
            {loading ? "模拟中…" : "模拟"}
          </Button>
        </CardContent>
      </Card>

      {error ? (
        <Card className="border border-destructive/30 bg-card ring-0">
          <CardContent className="flex items-center gap-3 py-4 text-sm text-muted-foreground">
            <AlertTriangle className="size-4 text-destructive" aria-hidden="true" />
            模拟失败：{error}
          </CardContent>
        </Card>
      ) : null}

      {!result ? (
        <Card className="border border-dashed border-border bg-card/60 ring-0">
          <CardContent className="flex min-h-52 flex-col items-center justify-center gap-3 text-center">
            <FlaskConical className="size-8 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="font-medium">选择场景并开始模拟</p>
              <p className="mt-1 text-sm text-muted-foreground">所有推演均在内存完成，不会修改数据库。</p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {[
              ["新增红色物料", result.summary.new_red_count, "text-risk-red"],
              ["新增橙色物料", result.summary.new_orange_count, "text-risk-orange"],
              ["缺口增量", integerFormatter.format(result.summary.total_gap_delta), "text-foreground"],
              ["金额敞口", moneyFormatter.format(result.summary.exposure_amount), "text-foreground"],
            ].map(([label, value, className]) => (
              <Card key={label} className="border border-border bg-card ring-0">
                <CardHeader><CardTitle className="text-sm text-muted-foreground">{label}</CardTitle></CardHeader>
                <CardContent><p className={`text-3xl font-semibold ${className}`}>{value}</p></CardContent>
              </Card>
            ))}
          </div>

          <Card className="border border-border bg-card ring-0">
            <CardHeader><CardTitle>恶化物料</CardTitle></CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader><TableRow><TableHead>物料</TableHead><TableHead>风险变化</TableHead><TableHead className="text-right">缺口变化</TableHead><TableHead className="text-right">采购份额</TableHead></TableRow></TableHeader>
                <TableBody>
                  {result.worsened_materials.map((item) => (
                    <TableRow key={item.material_pn}>
                      <TableCell className="font-mono text-xs">{item.material_pn}</TableCell>
                      <TableCell className="space-x-2"><Badge variant="outline" className={levelStyles[item.baseline_level]}>{item.baseline_level}</Badge><span>→</span><Badge variant="outline" className={levelStyles[item.scenario_level]}>{item.scenario_level}</Badge></TableCell>
                      <TableCell className="text-right tabular-nums">+{integerFormatter.format(item.gap_delta)}</TableCell>
                      <TableCell className="text-right tabular-nums">{item.split_pct.toFixed(1)}%</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card className="border border-border bg-card ring-0">
            <CardHeader><CardTitle>受影响 SKU</CardTitle></CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader><TableRow><TableHead>SKU</TableHead><TableHead className="text-right">受影响台数</TableHead><TableHead className="text-right">最近售价</TableHead><TableHead className="text-right">金额敞口</TableHead></TableRow></TableHeader>
                <TableBody>
                  {result.affected_skus.map((item) => (
                    <TableRow key={item.sku_id}><TableCell className="font-mono text-xs">{item.sku_id}</TableCell><TableCell className="text-right tabular-nums">{decimalFormatter.format(item.affected_units)}</TableCell><TableCell className="text-right tabular-nums">{moneyFormatter.format(item.unit_price)}</TableCell><TableCell className="text-right font-medium tabular-nums">{moneyFormatter.format(item.exposure_amount)}</TableCell></TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
                金额敞口为上限估算：物料缺口按窗口内 SKU 需求占比分摊并折算成品台数；同一 SKU 受多个物料影响时，仅取最大受限台数作为瓶颈，避免重复计数。
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </section>
  );
}
