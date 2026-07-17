import type { ReactNode } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { IngestValidatedRow, IngestValidationError } from "@/lib/schemas";

export type ValidationReportViewData = {
  total_rows: number;
  valid_count: number;
  error_count: number;
  errors: IngestValidationError[];
  preview: IngestValidatedRow[];
};

export function ValidationReportView({
  report,
  actions,
  errorAction,
}: {
  report: ValidationReportViewData;
  actions?: ReactNode;
  errorAction?: ReactNode;
}) {
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          ["总行数", report.total_rows],
          ["合法行", report.valid_count],
          ["错误项", report.error_count],
        ].map(([label, value]) => (
          <div className="rounded-lg border border-border p-4" key={label}>
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>

      {report.errors.length > 0 && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-destructive">
              错误明细
              {report.error_count > report.errors.length
                ? `（共 ${report.error_count} 条，仅显示前 ${report.errors.length} 条）`
                : null}
            </h3>
            {errorAction}
          </div>
          <div className="max-h-80 overflow-auto rounded-lg border border-destructive/25">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Excel 行号</TableHead>
                  <TableHead>字段</TableHead>
                  <TableHead>原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {report.errors.map((item, index) => (
                  <TableRow
                    className="bg-destructive/5"
                    key={`${item.row}-${item.field}-${index}`}
                  >
                    <TableCell className="tabular-nums">{item.row}</TableCell>
                    <TableCell className="font-mono text-xs">{item.field}</TableCell>
                    <TableCell className="text-destructive">{item.reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {report.preview.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">合法行预览（最多 20 行）</h3>
          <div className="overflow-x-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  {(["po_id", "material_pn", "supplier_id", "qty", "eta_date"] as const).map(
                    (column) => <TableHead key={column}>{column}</TableHead>,
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {report.preview.map((row) => (
                  <TableRow key={row.po_id}>
                    <TableCell className="font-mono text-xs">{row.po_id}</TableCell>
                    <TableCell className="font-mono text-xs">{row.material_pn}</TableCell>
                    <TableCell className="font-mono text-xs">{row.supplier_id}</TableCell>
                    <TableCell className="tabular-nums">{row.qty}</TableCell>
                    <TableCell>{row.eta_date}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {actions && <div className="border-t border-border pt-4">{actions}</div>}
    </div>
  );
}
