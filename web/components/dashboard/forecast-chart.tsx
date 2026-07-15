"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { getSkuForecast } from "@/lib/api";
import type { SkuForecast, SkuInfo } from "@/lib/schemas";

const chartConfig = {
  history: { label: "历史销量", color: "var(--chart-4)" },
  seasonal_naive: { label: "Seasonal Naive", color: "var(--chart-1)" },
  ets: { label: "ETS", color: "var(--chart-2)" },
  lightgbm: { label: "LightGBM", color: "var(--chart-3)" },
};

type ChartRow = {
  date: string;
  history: number | null;
  seasonal_naive: number | null;
  ets: number | null;
  lightgbm: number | null;
};

export function ForecastChart({ skus }: { skus: SkuInfo[] }) {
  const [selectedSku, setSelectedSku] = useState(skus[0]?.sku_id ?? "");
  const [forecast, setForecast] = useState<SkuForecast | null>(null);
  const [loading, setLoading] = useState(skus.length > 0);
  const [error, setError] = useState<string | null>(null);
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!selectedSku) return;
    let active = true;
    void getSkuForecast(selectedSku, 90)
      .then((result) => {
        if (active) setForecast(result);
      })
      .catch((requestError: unknown) => {
        if (active) {
          setForecast(null);
          setError(requestError instanceof Error ? requestError.message : "预测加载失败");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedSku]);

  const chartData = useMemo(() => mergeForecastData(forecast), [forecast]);

  function toggleSeries(dataKey: string) {
    setHiddenSeries((current) => {
      const next = new Set(current);
      if (next.has(dataKey)) next.delete(dataKey);
      else next.add(dataKey);
      return next;
    });
  }

  function changeSku(skuId: string) {
    setLoading(true);
    setError(null);
    setForecast(null);
    setSelectedSku(skuId);
  }

  return (
    <Card className="border border-border bg-card ring-0">
      <CardHeader className="border-b border-border">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <CardTitle className="text-[20px]">SKU 需求预测</CardTitle>
            <p className="text-xs text-muted-foreground">90 天历史与未来 28 天三模型预测</p>
          </div>
          <Select value={selectedSku} onValueChange={changeSku} disabled={skus.length === 0}>
            <SelectTrigger className="w-full sm:w-72" aria-label="选择 SKU">
              <SelectValue placeholder="选择 SKU" />
            </SelectTrigger>
            <SelectContent>
              {skus.map((sku) => (
                <SelectItem key={sku.sku_id} value={sku.sku_id}>
                  {sku.sku_id} · {sku.product_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </CardHeader>
      <CardContent className="pt-4">
        {error ? (
          <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
            预测曲线加载失败：{error}
          </div>
        ) : loading ? (
          <Skeleton className="h-96 w-full" />
        ) : chartData.length === 0 ? (
          <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
            暂无预测数据
          </div>
        ) : (
          <ChartContainer config={chartConfig} className="h-96 w-full aspect-auto">
            <LineChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="date"
                tickFormatter={(value: string) => value.slice(5)}
                tickLine={false}
                axisLine={false}
                minTickGap={24}
              />
              <YAxis tickLine={false} axisLine={false} width={44} domain={["auto", "auto"]} />
              <ChartTooltip
                content={<ChartTooltipContent labelFormatter={(label) => String(label)} />}
              />
              <Legend
                onClick={(entry) => {
                  if (typeof entry.dataKey === "string") toggleSeries(entry.dataKey);
                }}
                wrapperStyle={{ cursor: "pointer" }}
              />
              <Line
                dataKey="history"
                name="历史销量"
                type="monotone"
                stroke="var(--color-history)"
                strokeWidth={2}
                dot={false}
                connectNulls={false}
                hide={hiddenSeries.has("history")}
              />
              {(["seasonal_naive", "ets", "lightgbm"] as const).map((model) => (
                <Line
                  key={model}
                  dataKey={model}
                  name={chartConfig[model].label}
                  type="monotone"
                  stroke={`var(--color-${model})`}
                  strokeWidth={2}
                  strokeDasharray="6 4"
                  dot={false}
                  connectNulls={false}
                  hide={hiddenSeries.has(model)}
                />
              ))}
            </LineChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  );
}

function mergeForecastData(forecast: SkuForecast | null): ChartRow[] {
  if (forecast === null) return [];
  const rows = new Map<string, ChartRow>();
  const emptyRow = (date: string): ChartRow => ({
    date,
    history: null,
    seasonal_naive: null,
    ets: null,
    lightgbm: null,
  });

  for (const point of forecast.history) {
    const row = rows.get(point.date) ?? emptyRow(point.date);
    row.history = point.units_sold;
    rows.set(point.date, row);
  }
  for (const point of forecast.forecast) {
    const row = rows.get(point.date) ?? emptyRow(point.date);
    if (point.model_name in chartConfig) {
      row[point.model_name as keyof typeof chartConfig] = point.yhat;
    }
    rows.set(point.date, row);
  }
  return [...rows.values()].sort((left, right) => left.date.localeCompare(right.date));
}
