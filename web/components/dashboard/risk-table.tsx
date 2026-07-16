"use client";

import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { DiagnosisPanel } from "@/components/diagnose/diagnosis-panel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getRiskMaterialDetail, getRiskMaterials } from "@/lib/api";
import type { MaterialRisk, MaterialRiskDetail } from "@/lib/schemas";

type RiskLevel = MaterialRisk["risk_level"];
type LevelFilter = "ALL" | RiskLevel;

const levelStyles: Record<RiskLevel, string> = {
  RED: "border-risk-red/40 bg-risk-red/15 text-risk-red",
  ORANGE: "border-risk-orange/40 bg-risk-orange/15 text-risk-orange",
  YELLOW: "border-risk-yellow/40 bg-risk-yellow/15 text-risk-yellow",
  GREEN: "border-risk-green/40 bg-risk-green/15 text-risk-green",
};

const numberFormatter = new Intl.NumberFormat("zh-CN");

type RiskTableProps = {
  initialMaterials: MaterialRisk[];
  counts: Record<RiskLevel, number>;
};

export function RiskTable({ initialMaterials, counts }: RiskTableProps) {
  const [materials, setMaterials] = useState(initialMaterials);
  const [level, setLevel] = useState<LevelFilter>("ALL");
  const [commodity, setCommodity] = useState("ALL");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedPn, setSelectedPn] = useState<string | null>(null);
  const [detail, setDetail] = useState<MaterialRiskDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const commodities = useMemo(
    () => [...new Set(initialMaterials.map((item) => item.commodity))].sort(),
    [initialMaterials],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const nextSearch = search.trim();
      if (nextSearch !== debouncedSearch) {
        setLoading(true);
        setError(null);
        setDebouncedSearch(nextSearch);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [debouncedSearch, search]);

  useEffect(() => {
    let active = true;
    void getRiskMaterials({
      level: level === "ALL" ? undefined : level,
      commodity: commodity === "ALL" ? undefined : commodity,
      search: debouncedSearch || undefined,
      limit: 300,
    })
      .then((result) => {
        if (active) setMaterials(result);
      })
      .catch((requestError: unknown) => {
        if (active) {
          setMaterials([]);
          setError(requestError instanceof Error ? requestError.message : "加载失败");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [commodity, debouncedSearch, level]);

  useEffect(() => {
    if (selectedPn === null) return;
    let active = true;
    void getRiskMaterialDetail(selectedPn)
      .then((result) => {
        if (active) setDetail(result);
      })
      .catch((requestError: unknown) => {
        if (active) {
          setDetailError(requestError instanceof Error ? requestError.message : "详情加载失败");
        }
      });
    return () => {
      active = false;
    };
  }, [selectedPn]);

  const tabs: Array<{ value: LevelFilter; label: string; count: number }> = [
    { value: "ALL", label: "全部", count: Object.values(counts).reduce((sum, value) => sum + value, 0) },
    { value: "RED", label: "RED", count: counts.RED },
    { value: "ORANGE", label: "ORANGE", count: counts.ORANGE },
    { value: "YELLOW", label: "YELLOW", count: counts.YELLOW },
    { value: "GREEN", label: "GREEN", count: counts.GREEN },
  ];

  function changeLevel(value: string) {
    setLoading(true);
    setError(null);
    setLevel(value as LevelFilter);
  }

  function changeCommodity(value: string) {
    setLoading(true);
    setError(null);
    setCommodity(value);
  }

  function openDetail(materialPn: string) {
    setDetail(null);
    setDetailError(null);
    setSelectedPn(materialPn);
  }

  return (
    <>
      <Card className="border border-border bg-card ring-0">
        <CardHeader className="border-b border-border">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="space-y-1">
              <CardTitle className="text-[20px]">物料风险分级</CardTitle>
              <p className="text-xs text-muted-foreground">点击任一物料查看风险传导与供应明细</p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div className="relative sm:w-64">
                <Search
                  className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden="true"
                />
                <Input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索料号或名称"
                  className="pl-8"
                  aria-label="搜索料号或名称"
                />
              </div>
              <Select value={commodity} onValueChange={changeCommodity}>
                <SelectTrigger className="w-full sm:w-44" aria-label="Commodity 筛选">
                  <SelectValue placeholder="全部 Commodity" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">全部 Commodity</SelectItem>
                  {commodities.map((item) => (
                    <SelectItem key={item} value={item}>
                      {item}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <Tabs value={level} onValueChange={changeLevel}>
            <TabsList className="h-auto flex-wrap justify-start">
              {tabs.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value} className="px-3 py-1.5">
                  {tab.label}
                  <span className="text-xs text-muted-foreground">{tab.count}</span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </CardHeader>
        <CardContent className="px-0">
          {error ? (
            <div className="p-6 text-sm text-muted-foreground">风险物料加载失败：{error}</div>
          ) : materials.length === 0 && !loading ? (
            <div className="p-6 text-center text-sm text-muted-foreground">没有符合条件的物料</div>
          ) : (
            <div className="relative max-h-[560px] overflow-auto">
              {loading ? (
                <div className="absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden bg-muted">
                  <div className="h-full w-1/3 animate-pulse bg-primary" />
                </div>
              ) : null}
              <Table>
                <TableHeader className="sticky top-0 z-10 bg-card">
                  <TableRow>
                    <TableHead>料号</TableHead>
                    <TableHead>名称</TableHead>
                    <TableHead>Commodity</TableHead>
                    <TableHead>等级</TableHead>
                    <TableHead className="text-right">DOI 天</TableHead>
                    <TableHead className="text-right">LT 覆盖</TableHead>
                    <TableHead className="text-right">缺口件数</TableHead>
                    <TableHead>断料日</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {materials.map((material) => (
                    <TableRow
                      key={material.material_pn}
                      className="cursor-pointer"
                      tabIndex={0}
                      onClick={() => openDetail(material.material_pn)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") openDetail(material.material_pn);
                      }}
                    >
                      <TableCell className="font-mono text-xs">{material.material_pn}</TableCell>
                      <TableCell>{material.material_name}</TableCell>
                      <TableCell className="text-muted-foreground">{material.commodity}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={levelStyles[material.risk_level]}>
                          {material.risk_level}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{material.doi_days}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {material.lt_coverage.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {numberFormatter.format(material.gap_qty)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {material.gap_date ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Sheet open={selectedPn !== null} onOpenChange={(open) => !open && setSelectedPn(null)}>
        <SheetContent className="sm:max-w-2xl">
          <SheetHeader className="border-b border-border pr-12">
            <SheetTitle>{detail?.material_pn ?? selectedPn ?? "物料详情"}</SheetTitle>
            <SheetDescription>{detail?.material_name ?? "风险钻取详情"}</SheetDescription>
          </SheetHeader>
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-6 p-4">
              {detailError ? (
                <p className="text-sm text-muted-foreground">详情加载失败：{detailError}</p>
              ) : detail === null ? (
                <DetailSkeleton />
              ) : (
                <MaterialDetail detail={detail} />
              )}
            </div>
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

function MaterialDetail({ detail }: { detail: MaterialRiskDetail }) {
  return (
    <>
      <section className="space-y-2 rounded-lg border border-border bg-background p-4">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className={levelStyles[detail.risk_level]}>
            {detail.risk_level}
          </Badge>
          <span className="text-xs text-muted-foreground">{detail.commodity}</span>
        </div>
        <p className="text-sm leading-6 text-foreground">{detail.explanation}</p>
        <p className="font-mono text-xs text-muted-foreground">{detail.risk_reasons || "NO_RULE"}</p>
      </section>

      <DiagnosisPanel key={detail.material_pn} materialPn={detail.material_pn} />

      <DetailTable
        title="贡献 SKU"
        headers={["SKU", "需求贡献"]}
        rows={detail.top_skus.map((item) => [item.sku_id, numberFormatter.format(item.demand_qty)])}
      />
      <DetailTable
        title="供应商拆分"
        headers={["供应商", "份额", "交期", "MOQ"]}
        rows={detail.suppliers.map((item) => [
          item.supplier_name,
          `${item.split_pct}%`,
          `${item.lead_time_days} 天`,
          numberFormatter.format(item.moq),
        ])}
      />
      <DetailTable
        title="在途 PO"
        headers={["PO", "供应商", "数量", "ETA"]}
        rows={detail.open_pos.map((item) => [
          item.po_id,
          item.supplier_name,
          numberFormatter.format(item.qty),
          item.eta_date,
        ])}
        emptyText="无在途"
      />
    </>
  );
}

function DetailTable({
  title,
  headers,
  rows,
  emptyText = "暂无数据",
}: {
  title: string;
  headers: string[];
  rows: string[][];
  emptyText?: string;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {rows.length === 0 ? (
        <p className="rounded-lg border border-border p-4 text-sm text-muted-foreground">
          {emptyText}
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                {headers.map((header) => (
                  <TableHead key={header}>{header}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.join("-")}>
                  {row.map((cell, index) => (
                    <TableCell key={`${index}-${cell}`}>{cell}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}
