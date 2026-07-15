"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import type { RiskSummary } from "@/lib/schemas";

const commodityConfig = {
  red_count: { label: "红色", color: "var(--risk-red)" },
  orange_count: { label: "橙色", color: "var(--risk-orange)" },
  yellow_count: { label: "黄色", color: "var(--risk-yellow)" },
  green_count: { label: "绿色", color: "var(--risk-green)" },
};

const supplierConfig = {
  weighted_gap_qty: { label: "加权缺口量", color: "var(--chart-3)" },
};

const numberFormatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

export function ReportCharts({ summary }: { summary: RiskSummary }) {
  return (
    <section className="grid gap-4 2xl:grid-cols-2" aria-label="周报图表摘要">
      <Card className="report-chart-card border border-border bg-card ring-0">
        <CardHeader className="border-b border-border">
          <CardTitle className="text-[20px]">Commodity 风险分布</CardTitle>
          <p className="text-xs text-muted-foreground">各品类按风险等级堆叠对比</p>
        </CardHeader>
        <CardContent className="pt-4">
          <ChartContainer config={commodityConfig} className="h-80 w-full aspect-auto">
            <BarChart
              data={summary.by_commodity}
              margin={{ top: 8, right: 8, left: 0, bottom: 8 }}
            >
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis dataKey="commodity" tickLine={false} axisLine={false} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={36} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <ChartLegend content={<ChartLegendContent />} />
              {(Object.keys(commodityConfig) as Array<keyof typeof commodityConfig>).map(
                (dataKey) => (
                  <Bar
                    key={dataKey}
                    dataKey={dataKey}
                    isAnimationActive={false}
                    stackId="risk"
                    fill={`var(--color-${dataKey})`}
                    stroke="var(--card)"
                    strokeWidth={2}
                    radius={[4, 4, 4, 4]}
                  />
                ),
              )}
            </BarChart>
          </ChartContainer>
        </CardContent>
      </Card>

      <Card className="report-chart-card border border-border bg-card ring-0">
        <CardHeader className="border-b border-border">
          <CardTitle className="text-[20px]">供应商敞口 Top 5</CardTitle>
          <p className="text-xs text-muted-foreground">按加权缺口量从高到低排序</p>
        </CardHeader>
        <CardContent className="pt-4">
          <ChartContainer config={supplierConfig} className="h-80 w-full aspect-auto">
            <BarChart
              data={summary.top_suppliers}
              layout="vertical"
              margin={{ top: 8, right: 76, left: 8, bottom: 8 }}
            >
              <CartesianGrid horizontal={false} stroke="var(--border)" />
              <XAxis type="number" hide />
              <YAxis
                type="category"
                dataKey="supplier_name"
                tickLine={false}
                axisLine={false}
                width={178}
                tickFormatter={(value: string) => value.replace("Contoso Supply Partner ", "Contoso ")}
              />
              <ChartTooltip
                cursor={false}
                content={
                  <ChartTooltipContent
                    formatter={(value) => numberFormatter.format(Number(value))}
                  />
                }
              />
              <Bar
                dataKey="weighted_gap_qty"
                fill="var(--color-weighted_gap_qty)"
                isAnimationActive={false}
                radius={[0, 4, 4, 0]}
                maxBarSize={28}
              >
                <LabelList
                  dataKey="weighted_gap_qty"
                  position="right"
                  className="fill-foreground text-xs tabular-nums"
                  formatter={(value) => numberFormatter.format(Number(value ?? 0))}
                />
              </Bar>
            </BarChart>
          </ChartContainer>
        </CardContent>
      </Card>
    </section>
  );
}
