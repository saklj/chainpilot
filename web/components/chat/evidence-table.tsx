import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { VerdictMatch } from "@/lib/schemas";
import { cn } from "@/lib/utils";

function displayCell(value: unknown) {
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function EvidenceTable({
  columns,
  rows,
  rowCount,
  matched = [],
}: {
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  matched?: VerdictMatch[];
}) {
  const coordinates = new Set(matched.map((item) => `${item.row}:${item.column}`));
  const shownRows = rows.slice(0, 50);

  if (columns.length === 0) {
    return <p className="text-xs text-muted-foreground">查询未返回数据列</p>;
  }

  return (
    <div className="space-y-2">
      <div className="max-h-72 overflow-auto rounded-lg border border-border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column} className="font-mono text-xs">
                  {column}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shownRows.map((row, rowIndex) => (
              <TableRow key={rowIndex}>
                {columns.map((column, columnIndex) => (
                  <TableCell
                    key={`${column}:${columnIndex}`}
                    className={cn(
                      "font-mono text-xs tabular-nums",
                      coordinates.has(`${rowIndex}:${columnIndex}`) &&
                        "bg-primary/20 text-primary-foreground",
                    )}
                  >
                    {displayCell(row[columnIndex])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-[11px] text-muted-foreground">
        共 {rowCount} 行{rowCount > 50 ? "，仅展示前 50 行" : ""}
      </p>
    </div>
  );
}
