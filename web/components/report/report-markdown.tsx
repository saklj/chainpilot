import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const markdownComponents: Components = {
  h1: ({ children }) => (
    <h1 className="border-b border-border pb-4 text-[length:var(--font-headline-size)] font-semibold tracking-tight text-foreground">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-10 mb-4 text-[20px] font-medium tracking-tight text-foreground">
      {children}
    </h2>
  ),
  h3: ({ children }) => <h3 className="mt-6 mb-3 text-base font-medium">{children}</h3>,
  p: ({ children }) => (
    <p className="my-3 text-sm leading-7 text-foreground/90">{children}</p>
  ),
  ul: ({ children }) => <ul className="my-4 space-y-2 pl-5 text-sm leading-6">{children}</ul>,
  ol: ({ children }) => (
    <ol className="my-4 list-decimal space-y-2 pl-5 text-sm leading-6">{children}</ol>
  ),
  li: ({ children }) => <li className="pl-1 marker:text-muted-foreground">{children}</li>,
  table: ({ children }) => (
    <div className="report-table my-5 overflow-x-auto rounded-lg border border-border">
      <table className="w-full caption-bottom text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-muted/50 [&_tr]:border-b">{children}</thead>,
  tbody: ({ children }) => <tbody className="[&_tr:last-child]:border-0">{children}</tbody>,
  tr: ({ children }) => (
    <tr className="border-b border-border transition-colors hover:bg-muted/30">{children}</tr>
  ),
  th: ({ children }) => (
    <th className="h-10 px-3 text-left align-middle font-medium whitespace-nowrap text-foreground">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-3 py-2.5 align-middle font-mono text-xs whitespace-nowrap tabular-nums text-foreground/85">
      {children}
    </td>
  ),
  code: ({ children, className }) => (
    <code className={className ?? "rounded bg-muted px-1 py-0.5 font-mono text-xs"}>{children}</code>
  ),
  hr: () => <hr className="my-8 border-border" />,
};

export function ReportMarkdown({ content }: { content: string }) {
  return (
    <article>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </article>
  );
}
