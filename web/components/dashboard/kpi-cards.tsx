import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const numberFormatter = new Intl.NumberFormat("zh-CN");

type KpiCardsProps = {
  redCount: number;
  orangeCount: number;
  totalGapQty: number;
  redOrangePct: number;
  lightgbmMape: number;
  baselineImprovement: number;
};

export function KpiCards({
  redCount,
  orangeCount,
  totalGapQty,
  redOrangePct,
  lightgbmMape,
  baselineImprovement,
}: KpiCardsProps) {
  const metrics = [
    {
      label: "红色风险数",
      value: numberFormatter.format(redCount),
      valueClassName: "text-risk-red",
      note: "需立即处置",
    },
    {
      label: "橙色风险数",
      value: numberFormatter.format(orangeCount),
      valueClassName: "text-risk-orange",
      note: "仍可追料",
    },
    {
      label: "总缺口件数",
      value: numberFormatter.format(totalGapQty),
      valueClassName: "text-foreground",
      note: `红橙占比 ${redOrangePct.toFixed(1)}%`,
    },
    {
      label: "预测误差 MAPE",
      value: lightgbmMape.toFixed(1),
      valueClassName: "text-foreground",
      note: `较季节朴素基线改善 ${baselineImprovement.toFixed(2)}%`,
    },
  ] as const;

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map((metric) => (
        <Card key={metric.label} className="border border-border bg-card ring-0">
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {metric.label}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p
              className={`text-[length:var(--font-display-md-size)] font-semibold tracking-tight ${metric.valueClassName}`}
            >
              {metric.value}
            </p>
            <p className="text-xs text-muted-foreground">{metric.note}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
