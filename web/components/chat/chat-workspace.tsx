"use client";

import { ArrowUp, RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  AssistantMessage,
  type AssistantMessageState,
} from "@/components/chat/assistant-message";
import { Button } from "@/components/ui/button";
import { streamChat } from "@/lib/chat-stream";
import type { ChatEvent } from "@/lib/schemas";

type ConversationItem =
  | { id: string; role: "user"; content: string }
  | { id: string; role: "assistant"; state: AssistantMessageState };

const examples = [
  "当前 RED 风险的物料有几个？",
  "PN-00003 的缺口是多少？",
  "按 commodity 汇总最新风险。",
];

function newAssistant(id: string): AssistantMessageState {
  return {
    id,
    sql: null,
    columns: [],
    rows: [],
    rowCount: 0,
    draft: null,
    result: null,
    error: null,
  };
}

function reduceEvent(state: AssistantMessageState, event: ChatEvent): AssistantMessageState {
  switch (event.type) {
    case "stage":
      return state;
    case "sql":
      return { ...state, sql: event.sql };
    case "rows":
      return {
        ...state,
        columns: event.columns,
        rows: event.rows,
        rowCount: event.row_count,
      };
    case "answer":
      return { ...state, draft: event.answer };
    case "result":
      return {
        ...state,
        sql: event.result.sql ?? state.sql,
        columns: event.result.columns,
        rows: event.result.rows,
        rowCount: event.result.row_count,
        result: event.result,
      };
  }
}

export function ChatWorkspace() {
  const [items, setItems] = useState<ConversationItem[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [validation, setValidation] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items]);

  function updateAssistant(id: string, update: (state: AssistantMessageState) => AssistantMessageState) {
    setItems((current) =>
      current.map((item) =>
        item.role === "assistant" && item.id === id
          ? { ...item, state: update(item.state) }
          : item,
      ),
    );
  }

  async function send(rawQuestion = question) {
    const trimmed = rawQuestion.trim();
    if (pending) return;
    if (trimmed.length < 1 || trimmed.length > 500) {
      setValidation("问题需为 1～500 个字符");
      return;
    }

    const requestId = crypto.randomUUID();
    const assistantId = `${requestId}:assistant`;
    setItems((current) => [
      ...current,
      { id: requestId, role: "user", content: trimmed },
      { id: assistantId, role: "assistant", state: newAssistant(assistantId) },
    ]);
    setQuestion("");
    setValidation(null);
    setPending(true);

    try {
      await streamChat(trimmed, (event) => {
        updateAssistant(assistantId, (state) => reduceEvent(state, event));
      });
    } catch (error) {
      updateAssistant(assistantId, (state) => ({
        ...state,
        error: error instanceof Error ? error.message : "网络连接失败",
      }));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="mx-auto flex min-h-[calc(100svh-7.5rem)] w-full max-w-5xl flex-col">
      <header className="border-b border-border pb-4">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">M5 · T4</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-[length:var(--font-headline-size)] font-semibold tracking-tight">
              Chat 问数
            </h1>
            <p className="mt-1 text-xs text-muted-foreground">
              每次提问独立求解，不携带上下文
            </p>
          </div>
          {items.length > 0 && !pending ? (
            <Button variant="ghost" size="sm" onClick={() => setItems([])}>
              <RotateCcw aria-hidden="true" /> 清空消息
            </Button>
          ) : null}
        </div>
      </header>

      <div className="flex-1 space-y-5 py-6" aria-live="polite">
        {items.length === 0 ? (
          <div className="flex min-h-80 flex-col items-center justify-center gap-5 text-center">
            <span className="flex size-12 items-center justify-center rounded-xl border border-primary/30 bg-primary/10 text-primary">
              <Sparkles className="size-5" aria-hidden="true" />
            </span>
            <div className="space-y-1">
              <h2 className="text-lg font-medium">从供应链数据中寻找答案</h2>
              <p className="text-sm text-muted-foreground">试试下面这些已验证的问题</p>
            </div>
            <div className="flex max-w-2xl flex-wrap justify-center gap-2">
              {examples.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => void send(example)}
                  className="rounded-full border border-border bg-card px-3 py-2 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : (
          items.map((item) =>
            item.role === "user" ? (
              <article key={item.id} className="flex justify-end">
                <p className="max-w-2xl whitespace-pre-wrap rounded-xl bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground">
                  {item.content}
                </p>
              </article>
            ) : (
              <AssistantMessage key={item.id} message={item.state} />
            ),
          )
        )}
        <div ref={bottomRef} />
      </div>

      <div className="sticky bottom-0 border-t border-border bg-background/95 pt-4 pb-1 backdrop-blur">
        <div className="relative rounded-xl border border-input bg-card focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30">
          <textarea
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              if (validation) setValidation(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            disabled={pending}
            maxLength={500}
            rows={2}
            aria-label="输入供应链数据问题"
            aria-invalid={validation !== null}
            placeholder="输入供应链数据问题，Enter 发送，Shift+Enter 换行"
            className="min-h-20 w-full resize-none bg-transparent px-4 py-3 pr-14 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
          />
          <Button
            size="icon"
            disabled={pending || question.trim().length === 0}
            onClick={() => void send()}
            className="absolute right-3 bottom-3"
            aria-label="发送问题"
          >
            <ArrowUp aria-hidden="true" />
          </Button>
        </div>
        <div className="flex min-h-6 items-center justify-between px-1 pt-1 text-[11px] text-muted-foreground">
          <span className={validation ? "text-destructive" : undefined}>
            {validation ?? (pending ? "正在处理，请等待终帧校验" : "答案中的数字将与查询证据逐一核验")}
          </span>
          <span>{question.length}/500</span>
        </div>
      </div>
    </section>
  );
}
