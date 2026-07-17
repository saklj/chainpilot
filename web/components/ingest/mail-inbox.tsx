"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  LoaderCircle,
  Mail,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ValidationReportView } from "@/components/ingest/validation-report-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Toaster } from "@/components/ui/sonner";
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
  confirmIngestMailItem,
  getIngestMailConfig,
  getIngestMailItem,
  getIngestMailItems,
  pollIngestMail,
  rejectIngestMailItem,
} from "@/lib/api";
import type {
  IngestMailConfig,
  IngestMailItem,
  IngestMailItemDetail,
  IngestMailStatus,
} from "@/lib/schemas";

const STATUS_LABELS: Record<IngestMailStatus, string> = {
  pending_review: "待确认",
  blocked: "已拦截",
  invalid_file: "文件无效",
  confirmed: "已确认",
  rejected: "已拒绝",
};

const STATUS_STYLES: Record<IngestMailStatus, string> = {
  pending_review: "border-risk-orange/40 bg-risk-orange/10 text-risk-orange",
  blocked: "border-destructive/40 bg-destructive/10 text-destructive",
  invalid_file: "border-destructive/40 bg-destructive/10 text-destructive",
  confirmed: "border-risk-green/40 bg-risk-green/10 text-risk-green",
  rejected: "border-border bg-muted text-muted-foreground",
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && typeof error.detail === "object" && error.detail !== null) {
    const detail = error.detail as { message?: unknown };
    if (typeof detail.message === "string") return detail.message;
  }
  return error instanceof Error ? error.message : "邮件接入操作失败";
}

export function MailInbox({ onBatchChanged }: { onBatchChanged: () => Promise<void> }) {
  const [config, setConfig] = useState<IngestMailConfig | null>(null);
  const [items, setItems] = useState<IngestMailItem[]>([]);
  const [selected, setSelected] = useState<IngestMailItemDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refreshItems() {
    setItems(await getIngestMailItems());
  }

  useEffect(() => {
    let active = true;
    Promise.all([getIngestMailConfig(), getIngestMailItems()])
      .then(([nextConfig, nextItems]) => {
        if (!active) return;
        setConfig(nextConfig);
        setItems(nextItems);
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

  async function poll() {
    if (busy) return;
    setBusy("poll");
    setError(null);
    try {
      const result = await pollIngestMail();
      toast.success(
        `收取完成：新增 ${result.new_items} / 拦截 ${result.blocked} / 重复 ${result.duplicates} / 坏文件 ${result.invalid_files}`,
      );
      await refreshItems();
    } catch (requestError) {
      const message = errorMessage(requestError);
      setError(message);
      toast.error(message);
    } finally {
      setBusy(null);
    }
  }

  async function openItem(itemId: string) {
    if (busy) return;
    setBusy(`detail:${itemId}`);
    setError(null);
    try {
      setSelected(await getIngestMailItem(itemId));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(null);
    }
  }

  async function confirmItem(itemId: string) {
    if (busy) return;
    setBusy(`confirm:${itemId}`);
    setError(null);
    try {
      const result = await confirmIngestMailItem(itemId);
      toast.success(`已确认导入 ${result.row_count} 行，批次 ${result.batch_id}`);
      setSelected(null);
      await Promise.all([refreshItems(), onBatchChanged()]);
    } catch (requestError) {
      const message = errorMessage(requestError);
      setError(message);
      toast.error(message);
      try {
        setSelected(await getIngestMailItem(itemId));
      } catch {
        setSelected(null);
      }
    } finally {
      setBusy(null);
    }
  }

  async function rejectItem(itemId: string) {
    if (busy) return;
    setBusy(`reject:${itemId}`);
    setError(null);
    try {
      await rejectIngestMailItem(itemId);
      toast.success("邮件条目已拒绝，未导入任何数据");
      setSelected(null);
      await refreshItems();
    } catch (requestError) {
      const message = errorMessage(requestError);
      setError(message);
      toast.error(message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="border border-border bg-card ring-0">
      <Toaster position="top-right" />
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Mail className="size-5" aria-hidden="true" />
            邮件收件箱
          </CardTitle>
          <Button onClick={poll} disabled={busy === "poll" || loading}>
            {busy === "poll" ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="size-4" aria-hidden="true" />
            )}
            收取邮件
          </Button>
        </div>
        {config && (
          <p className="text-xs text-muted-foreground">
            来源：{config.source === "imap" ? "IMAP 邮箱" : "本地目录 data/inbox"} · 定时轮询：
            {config.scheduled_poll_enabled ? `开启（每 ${config.poll_seconds} 秒）` : "关闭"} ·
            发件人白名单：{config.allowed_senders_configured ? "已配置" : "未配置（全部拦截）"}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {loading ? (
          <p className="flex items-center text-sm text-muted-foreground">
            <LoaderCircle className="mr-2 size-4 animate-spin" aria-hidden="true" />
            正在加载邮件接入状态…
          </p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无邮件接入记录。</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>发件人</TableHead>
                  <TableHead>主题 / 文件名</TableHead>
                  <TableHead>收件时间</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">合法 / 错误</TableHead>
                  <TableHead>批次</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.item_id}>
                    <TableCell>{item.sender}</TableCell>
                    <TableCell className="min-w-52">
                      <p>{item.subject || "（无主题）"}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{item.filename}</p>
                      {item.error_message && (
                        <p className="mt-1 text-xs text-destructive">{item.error_message}</p>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {new Date(item.received_at).toLocaleString("zh-CN")}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={STATUS_STYLES[item.status]}>
                        {STATUS_LABELS[item.status]}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {item.valid_count} / {item.error_count}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{item.batch_id ?? "—"}</TableCell>
                    <TableCell className="text-right">
                      {item.status === "pending_review" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => openItem(item.item_id)}
                          disabled={busy === `detail:${item.item_id}`}
                        >
                          {busy === `detail:${item.item_id}` ? (
                            <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                          ) : (
                            <Eye className="size-3.5" aria-hidden="true" />
                          )}
                          查看报告
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {selected?.fresh_report && (
          <div className="space-y-4 rounded-lg border border-border bg-muted/15 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-semibold">确认前实时校验报告</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {selected.sender} · {selected.filename}。此报告已按当前数据库重新校验。
                </p>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setSelected(null)}>
                关闭
              </Button>
            </div>
            <ValidationReportView
              report={selected.fresh_report}
              actions={
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    onClick={() => confirmItem(selected.item_id)}
                    disabled={
                      selected.fresh_report.valid_count === 0 ||
                      busy === `confirm:${selected.item_id}`
                    }
                  >
                    {busy === `confirm:${selected.item_id}` ? (
                      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 className="size-4" aria-hidden="true" />
                    )}
                    确认导入 {selected.fresh_report.valid_count} 行
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => rejectItem(selected.item_id)}
                    disabled={busy === `reject:${selected.item_id}`}
                  >
                    <XCircle className="size-4" aria-hidden="true" />
                    拒绝
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    邮件轮询不会自动导入；只有这里的人工确认会写入 open_po。
                  </span>
                </div>
              }
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
