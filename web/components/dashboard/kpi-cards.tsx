import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const numberFormatter = new Intl.NumberFormat("zh-CN");

type KpiCardsProps = {
  redCount: number;
  orangeCount: number;
  totalGapQty: number;
  redOrangePct: number;
  forecastAccuracy: number;
  wmapeImprovement: number;
  lightgbmMape: number;
  mapeImprovement: number;
  lightgbmWrmsse: number;
};

export function KpiCards({
  redCount,
  orangeCount,
  totalGapQty,
  redOrangePct,
  forecastAccuracy,
  wmapeImprovement,
  lightgbmMape,
  mapeImprovement,
  lightgbmWrmsse,
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
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Card
              tabIndex={0}
              className="border border-border bg-card ring-0 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  预测准确度
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-[length:var(--font-display-md-size)] font-semibold tracking-tight text-foreground">
                  {forecastAccuracy.toFixed(1)}%
                </p>
                <p className="text-xs text-muted-foreground">
                  较朴素基线改善 {wmapeImprovement.toFixed(1)}%
                </p>
              </CardContent>
            </Card>
          </TooltipTrigger>
          <TooltipContent className="max-w-sm leading-relaxed">
            口径：1−WMAPE（销量加权），3 折滚动回测，日粒度 SKU 级；MAPE{
              " "
            }
            {lightgbmMape.toFixed(1)}（较基线改善 {mapeImprovement.toFixed(2)}%）；WRMSSE{
              " "
            }
            {lightgbmWrmsse.toFixed(3)}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
