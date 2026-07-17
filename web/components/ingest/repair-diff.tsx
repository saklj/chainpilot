import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { IngestRepair } from "@/lib/schemas";

const RULE_LABELS: Record<IngestRepair["rule_name"], string> = {
  date_format: "日期格式",
  qty_format: "数量格式",
  key_normalize: "键值规范化",
};

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

export function RepairDiff({ repairs, remainingErrors }: { repairs: IngestRepair[]; remainingErrors: number }) {
  return (
    <div className="space-y-3 rounded-lg border border-risk-green/25 bg-risk-green/5 p-4">
      <div>
        <h3 className="font-semibold">自动修复 diff</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          已修复 {repairs.length} 处格式问题；剩余 {remainingErrors}
          条需人工处理（未知物料、负数量等不猜测）。
        </p>
      </div>
      {repairs.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Excel 行号</TableHead>
                <TableHead>字段</TableHead>
                <TableHead>原值</TableHead>
                <TableHead>新值</TableHead>
                <TableHead>规则</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {repairs.map((repair, index) => (
                <TableRow key={`${repair.row}-${repair.field}-${index}`}>
                  <TableCell className="tabular-nums">{repair.row}</TableCell>
                  <TableCell className="font-mono text-xs">{repair.field}</TableCell>
                  <TableCell className="text-muted-foreground line-through">
                    {text(repair.original_value)}
                  </TableCell>
                  <TableCell>{text(repair.new_value)}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{RULE_LABELS[repair.rule_name]}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">未找到可安全自动修复的格式问题。</p>
      )}
    </div>
  );
}
