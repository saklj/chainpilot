"use client";

import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  LoaderCircle,
  RotateCcw,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

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
import {
  ApiError,
  confirmIngest,
  getIngestBatches,
  getIngestTemplate,
  previewIngestTemplate,
  rollbackIngest,
  saveIngestTemplate,
  validateIngestFile,
} from "@/lib/api";
import type {
  IngestBatch,
  IngestImportResult,
  IngestTargetColumn,
  IngestTemplatePreview,
  IngestTemplateState,
  IngestValidationReport,
} from "@/lib/schemas";

const TARGETS: { key: IngestTargetColumn; label: string }[] = [
  { key: "po_id", label: "采购单号" },
  { key: "material_pn", label: "物料号" },
  { key: "supplier_id", label: "供应商" },
  { key: "qty", label: "数量" },
  { key: "eta_date", label: "预计到货日" },
];
const UNMAPPED = "__unmapped__";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && typeof error.detail === "object" && error.detail !== null) {
    const detail = error.detail as { message?: unknown };
    if (typeof detail.message === "string") return detail.message;
  }
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function UploadField({
  label,
  loading,
  onFile,
}: {
  label: string;
  loading: boolean;
  onFile: (file: File) => void;
}) {
  // The native file input is hidden: its built-in "未选择任何文件" text cannot be
  // customized, and it gets cleared after each pick so re-picking the same file
  // still fires change. A styled button + tracked filename replace it.
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="space-y-2 text-sm">
      <span className="block font-medium">{label}</span>
      <Input
        ref={inputRef}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            setFileName(file.name);
            onFile(file);
          }
          event.target.value = "";
        }}
      />
      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          disabled={loading}
          onClick={() => inputRef.current?.click()}
        >
          <Upload aria-hidden="true" />
          选择文件
        </Button>
        {fileName ? (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <FileSpreadsheet className="size-4 shrink-0" aria-hidden="true" />
            {fileName}
          </span>
        ) : null}
      </div>
      <span className="block text-xs text-muted-foreground">
        仅支持 .xlsx，最大 20MB、50,000 行。
      </span>
    </div>
  );
}

export function IngestWorkspace() {
  const [template, setTemplate] = useState<IngestTemplateState | null>(null);
  const [batches, setBatches] = useState<IngestBatch[]>([]);
  const [templatePreview, setTemplatePreview] = useState<IngestTemplatePreview | null>(null);
  const [mapping, setMapping] = useState<Record<IngestTargetColumn, string>>({
    po_id: "",
    material_pn: "",
    supplier_id: "",
    qty: "",
    eta_date: "",
  });
  const [report, setReport] = useState<IngestValidationReport | null>(null);
  const [importResult, setImportResult] = useState<IngestImportResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refreshBatches() {
    setBatches(await getIngestBatches());
  }

  useEffect(() => {
    let active = true;
    Promise.all([getIngestTemplate(), getIngestBatches()])
      .then(([nextTemplate, nextBatches]) => {
        if (!active) return;
        setTemplate(nextTemplate);
        setBatches(nextBatches);
      })
      .catch((requestError: unknown) => {
        if (active) setError(errorMessage(requestError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function previewTemplate(file: File) {
    setBusy("template-preview");
    setError(null);
    try {
      const preview = await previewIngestTemplate(file);
      setTemplatePreview(preview);
      setMapping(
        Object.fromEntries(
          TARGETS.map(({ key }) => [key, preview.suggested_mapping[key] ?? ""]),
        ) as Record<IngestTargetColumn, string>,
      );
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(null);
    }
  }

  async function registerTemplate() {
    if (!templatePreview || busy) return;
    const sources = TARGETS.map(({ key }) => mapping[key]);
    if (sources.some((source) => !source)) {
      setError("请为 5 个目标列完成映射后再保存。");
      return;
    }
    if (new Set(sources).size !== sources.length) {
      setError("同一个源列不能映射到多个目标列。");
      return;
    }
    setBusy("template-save");
    setError(null);
    try {
      const saved = await saveIngestTemplate(mapping);
      setTemplate(saved);
      setTemplatePreview(null);
      await refreshBatches();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(null);
    }
  }

  async function validateUpload(file: File) {
    setBusy("validate");
    setError(null);
    setImportResult(null);
    setReport(null);
    try {
      setReport(await validateIngestFile(file));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(null);
    }
  }

  async function confirmUpload() {
    if (!report || report.valid_count === 0 || busy) return;
    setBusy("confirm");
    setError(null);
    try {
      const result = await confirmIngest(report.validation_token);
      setImportResult(result);
      setReport(null);
      await refreshBatches();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(null);
    }
  }

  async function rollback(batchId: string) {
    if (busy) return;
    setBusy(`rollback:${batchId}`);
    setError(null);
    try {
      await rollbackIngest(batchId);
      if (importResult?.batch_id === batchId) setImportResult(null);
      await refreshBatches();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-72 items-center justify-center text-sm text-muted-foreground">
        <LoaderCircle className="mr-2 size-4 animate-spin" aria-hidden="true" />
        正在加载数据接入配置…
      </div>
    );
  }

  return (
    <section className="mx-auto w-full max-w-7xl space-y-6">
      <header className="space-y-2 border-b border-border pb-5">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Controlled ingestion
        </p>
        <h1 className="text-[length:var(--font-headline-size)] font-semibold tracking-tight">
          Excel 数据接入
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          先注册历史样例的列映射，再以纯代码逐行校验新文件；只有人工确认后才写入 open_po。
        </p>
      </header>

      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}

      {!template?.exists ? (
        <Card className="border border-border bg-card ring-0">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileSpreadsheet className="size-5" aria-hidden="true" />
              第一步：注册历史样例模板
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <UploadField
              label="上传一份已经整理好的历史 Excel"
              loading={busy === "template-preview"}
              onFile={previewTemplate}
            />
            {busy === "template-preview" && (
              <p className="flex items-center text-sm text-muted-foreground">
                <LoaderCircle className="mr-2 size-4 animate-spin" aria-hidden="true" />
                正在读取列名并生成映射建议…
              </p>
            )}
            {templatePreview && (
              <div className="space-y-4">
                <div className="overflow-x-auto rounded-lg border border-border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>open_po 目标列</TableHead>
                        <TableHead>业务含义</TableHead>
                        <TableHead>确认源列</TableHead>
                        <TableHead>建议来源</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {TARGETS.map(({ key, label }) => (
                        <TableRow key={key}>
                          <TableCell className="font-mono text-xs">{key}</TableCell>
                          <TableCell>{label}</TableCell>
                          <TableCell className="min-w-64">
                            <Select
                              value={mapping[key] || UNMAPPED}
                              onValueChange={(value) =>
                                setMapping((current) => ({
                                  ...current,
                                  [key]: value === UNMAPPED ? "" : value,
                                }))
                              }
                            >
                              <SelectTrigger className="w-full" aria-label={`${label}源列`}>
                                <SelectValue placeholder="请选择源列" />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value={UNMAPPED}>未映射</SelectItem>
                                {templatePreview.source_columns.map((column) => (
                                  <SelectItem value={column} key={column}>
                                    {column}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </TableCell>
                          <TableCell>
                            {templatePreview.suggestion_sources[key] === "llm" ? (
                              <Badge variant="outline">LLM 建议</Badge>
                            ) : templatePreview.suggestion_sources[key] === "deterministic" ? (
                              <Badge variant="secondary">规则匹配</Badge>
                            ) : (
                              <span className="text-xs text-muted-foreground">等待人工选择</span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="text-xs text-muted-foreground">
                  所有自动结果都只是建议；保存前请人工确认。LLM 仅参与此配置步骤，不接触日常数据行。
                </p>
                <Button onClick={registerTemplate} disabled={busy === "template-save"}>
                  {busy === "template-save" ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <CheckCircle2 className="size-4" aria-hidden="true" />
                  )}
                  保存确认后的模板
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          <Card className="border border-border bg-card ring-0">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Upload className="size-5" aria-hidden="true" />
                上传并校验新批次
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <UploadField
                label="选择日常 Excel 文件"
                loading={busy === "validate"}
                onFile={validateUpload}
              />
              {busy === "validate" && (
                <p className="flex items-center text-sm text-muted-foreground">
                  <LoaderCircle className="mr-2 size-4 animate-spin" aria-hidden="true" />
                  正在逐行校验，尚未写入数据库…
                </p>
              )}
              {report && (
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
                      <h3 className="text-sm font-semibold text-destructive">
                        错误明细
                        {report.error_count > report.errors.length
                          ? `（共 ${report.error_count} 条，仅显示前 ${report.errors.length} 条）`
                          : null}
                      </h3>
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
                              <TableRow className="bg-destructive/5" key={`${item.row}-${item.field}-${index}`}>
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
                              {TARGETS.map(({ key }) => <TableHead key={key}>{key}</TableHead>)}
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

                  <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
                    <Button
                      onClick={confirmUpload}
                      disabled={report.valid_count === 0 || busy === "confirm"}
                    >
                      {busy === "confirm" && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}
                      确认导入 {report.valid_count} 行
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      错误行不会导入；点击确认前数据库保持不变。
                    </span>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {importResult && (
            <Card className="border border-risk-green/30 bg-risk-green/5 ring-0">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <CheckCircle2 className="size-5 text-risk-green" aria-hidden="true" />
                  导入成功
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 text-sm">
                <p>
                  已导入 <strong>{importResult.row_count}</strong> 行，批次号：
                  <span className="ml-2 font-mono text-xs">{importResult.batch_id}</span>
                </p>
                <p className="rounded-md border border-risk-orange/25 bg-risk-orange/5 p-3 text-muted-foreground">
                  风险评估将于下次引擎运行时反映本批在途；What-if 等实时读取 open_po 的功能会立即看到变化。
                </p>
                <Button
                  variant="outline"
                  onClick={() => rollback(importResult.batch_id)}
                  disabled={busy === `rollback:${importResult.batch_id}`}
                >
                  <RotateCcw className="size-4" aria-hidden="true" />
                  撤销本批导入
                </Button>
              </CardContent>
            </Card>
          )}

          <Card className="border border-border bg-card ring-0">
            <CardHeader><CardTitle className="text-base">历史导入批次</CardTitle></CardHeader>
            <CardContent>
              {batches.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无已导入批次。</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>批次号</TableHead>
                        <TableHead>文件名</TableHead>
                        <TableHead className="text-right">行数</TableHead>
                        <TableHead>导入时间</TableHead>
                        <TableHead className="text-right">操作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {batches.map((batch) => (
                        <TableRow key={batch.batch_id}>
                          <TableCell className="font-mono text-xs">{batch.batch_id}</TableCell>
                          <TableCell>{batch.filename}</TableCell>
                          <TableCell className="text-right tabular-nums">{batch.row_count}</TableCell>
                          <TableCell>{new Date(batch.created_at).toLocaleString("zh-CN")}</TableCell>
                          <TableCell className="text-right">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => rollback(batch.batch_id)}
                              disabled={busy === `rollback:${batch.batch_id}`}
                            >
                              <RotateCcw className="size-3.5" aria-hidden="true" />
                              撤销
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </section>
  );
}
