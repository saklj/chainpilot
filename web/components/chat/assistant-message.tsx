import { Bot, CheckCircle2, ChevronDown, LoaderCircle, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { EvidenceTable } from "@/components/chat/evidence-table";
import { Badge } from "@/components/ui/badge";
import type { ChatResult, VerdictMatch } from "@/lib/schemas";

export type AssistantMessageState = {
  id: string;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  draft: string | null;
  result: ChatResult | null;
  error: string | null;
};

const refusalText: Record<string, string> = {
  out_of_scope: "数据里没有可用于回答这项问题的信息。",
  generation_failed: "没能生成可靠查询，请换一种问法再试。",
  sql_rejected: "查询未通过安全检查，请调整问题后重试。",
  guardrail_failed: "答案中的数字未通过证据校验，已停止展示。",
};

function normalizedEvidence(value: string) {
  const chineseDate = value.match(/^(\d{4})年(\d{1,2})月(\d{1,2})日$/);
  if (chineseDate) {
    return `${chineseDate[1]}-${chineseDate[2].padStart(2, "0")}-${chineseDate[3].padStart(2, "0")}`;
  }
  return value.replace(/[,，\s]/g, "").replace("％", "%");
}

function HighlightedAnswer({ answer, matched }: { answer: string; matched: VerdictMatch[] }) {
  const values = new Set(matched.map((item) => normalizedEvidence(item.value)));
  if (values.size === 0) return answer;
  const tokens = answer.split(
    /(\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|(?:±|[+\-−])?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?[%％]?)/g,
  );
  return tokens.map((token, index): ReactNode =>
    values.has(normalizedEvidence(token)) ? (
      <mark
        key={`${token}:${index}`}
        className="rounded bg-primary/20 px-0.5 text-primary-foreground"
      >
        {token}
      </mark>
    ) : (
      token
    ),
  );
}

function SqlBlock({ sql }: { sql: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-border bg-background p-3 font-mono text-xs leading-5 text-muted-foreground">
      <code>{sql}</code>
    </pre>
  );
}

export function AssistantMessage({ message }: { message: AssistantMessageState }) {
  const { result } = message;
  const matched = result?.verdict?.matched ?? [];
  const finalSql = result?.final_sql ?? message.sql;
  const answer = result?.refused
    ? refusalText[result.refusal_reason ?? ""] ?? result.answer
    : result?.answer ?? message.draft;

  return (
    <article className="flex items-start gap-3">
      <span className="mt-1 flex size-8 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-primary">
        <Bot className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 max-w-4xl flex-1 space-y-3 rounded-xl border border-border bg-card p-4">
        {message.error ? (
          <div className="space-y-1">
            <p className="text-sm font-medium text-destructive">请求中断</p>
            <p className="text-sm text-muted-foreground">{message.error}，请重试。</p>
          </div>
        ) : null}

        {!message.error && message.sql === null && result === null ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            正在生成 SQL…
          </p>
        ) : null}

        {result === null && message.sql ? (
          <section className="space-y-2">
            <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <CheckCircle2 className="size-3.5 text-primary" aria-hidden="true" /> SQL 已生成
            </p>
            <SqlBlock sql={message.sql} />
          </section>
        ) : null}

        {result === null && message.columns.length > 0 ? (
          <section className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">查询结果</p>
            <EvidenceTable
              columns={message.columns}
              rows={message.rows}
              rowCount={message.rowCount}
            />
          </section>
        ) : null}

        {result === null && message.draft ? (
          <section className="space-y-2">
            <Badge variant="outline" className="text-muted-foreground">
              <LoaderCircle className="size-3 animate-spin" aria-hidden="true" /> 数字校验中
            </Badge>
            <p className="whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
              {message.draft}
            </p>
          </section>
        ) : null}

        {result && answer ? (
          <section className="space-y-2">
            {!result.refused && result.verdict?.verdict === "pass" ? (
              <Badge variant="outline" className="border-primary/40 bg-primary/10 text-primary">
                <ShieldCheck className="size-3" aria-hidden="true" /> 数字已核验
              </Badge>
            ) : null}
            <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
              {result.refused ? answer : <HighlightedAnswer answer={answer} matched={matched} />}
            </p>
          </section>
        ) : null}

        {result && finalSql ? (
          <details open className="group rounded-lg border border-border bg-background">
            <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-xs font-medium text-muted-foreground">
              证据与 SQL
              <ChevronDown className="size-4 transition-transform group-open:rotate-180" />
            </summary>
            <div className="space-y-3 border-t border-border p-3">
              <SqlBlock sql={finalSql} />
              <EvidenceTable
                columns={result.columns}
                rows={result.rows}
                rowCount={result.row_count}
                matched={matched}
              />
            </div>
          </details>
        ) : null}

        {result ? (
          <p className="text-[11px] text-muted-foreground">
            Tokens · 输入 {result.usage.prompt_tokens} · 输出 {result.usage.completion_tokens} · 合计{" "}
            {result.usage.prompt_tokens + result.usage.completion_tokens}
          </p>
        ) : null}
      </div>
    </article>
  );
}
