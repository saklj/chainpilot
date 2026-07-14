"""Prompt construction, SQL extraction, and CLI for natural-language queries."""

from __future__ import annotations

import re
import string
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

if __package__:
    from .glossary import SCHEMA_CARDS, load_glossary, render_glossary
    from .llm import DeepSeekClient, LLMResult, TokenUsage
    from .safe_sql import SafeResult, execute_safe
else:
    from glossary import SCHEMA_CARDS, load_glossary, render_glossary
    from llm import DeepSeekClient, LLMResult, TokenUsage
    from safe_sql import SafeResult, execute_safe

Message = dict[str, str]
NL2SQLStatus = Literal["ok", "no_answer", "invalid_format", "multiple_sql_blocks"]


class ChatLLM(Protocol):
    def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        timeout: float = 30,
    ) -> LLMResult: ...


@dataclass(frozen=True)
class FewShot:
    question: str
    answer: str


@dataclass(frozen=True)
class NL2SQLResult:
    status: NL2SQLStatus
    sql: str | None
    raw_response: str
    usage: TokenUsage


FEW_SHOTS: Final[tuple[FewShot, ...]] = (
    FewShot(
        "当前哪些物料是红色风险？",
        """```sql
SELECT r.material_pn, m.material_name, r.doi_days, r.gap_qty, r.gap_date
FROM material_risk r JOIN materials m USING (material_pn)
WHERE r.eval_date = (SELECT max(eval_date) FROM material_risk)
  AND r.risk_level = 'RED'
ORDER BY r.gap_qty DESC
```""",
    ),
    FewShot(
        "DOI 最低的物料的库存天数和缺口是多少？",
        """```sql
SELECT material_pn, doi_days, gap_qty, gap_date
FROM material_risk
WHERE eval_date = (SELECT max(eval_date) FROM material_risk)
ORDER BY doi_days, material_pn
LIMIT 1
```""",
    ),
    FewShot(
        "列出当前的单源物料及供应商集中度。",
        """```sql
SELECT ss.material_pn, max(ss.split_pct) AS supplier_concentration,
       min(s.supplier_name) AS supplier_name
FROM supply_split ss JOIN suppliers s USING (supplier_id)
GROUP BY ss.material_pn
HAVING count(*) = 1
ORDER BY supplier_concentration DESC, ss.material_pn
```""",
    ),
    FewShot(
        "按 commodity 汇总最新的红橙黄绿风险数量和缺口。",
        """```sql
SELECT eval_date, commodity, red_count, orange_count, yellow_count, green_count,
       total_gap_qty
FROM v_risk_by_commodity
WHERE eval_date = (SELECT max(eval_date) FROM material_risk)
ORDER BY red_count DESC, orange_count DESC, commodity
```""",
    ),
    FewShot(
        "比较各预测模型未来 28 天总销量与最近 28 天实际销量。",
        """```sql
WITH actual AS (
  SELECT sum(units_sold) AS actual_units
  FROM sales_daily
  WHERE date > (SELECT max(date) FROM sales_daily) - INTERVAL 28 DAY
)
SELECT f.model_name, sum(f.yhat) AS forecast_units, max(a.actual_units) AS actual_units
FROM forecast_daily f CROSS JOIN actual a
GROUP BY f.model_name
ORDER BY f.model_name
```""",
    ),
    FewShot(
        "还没到货的在途 PO 按供应商汇总是多少？",
        """```sql
SELECT s.supplier_id, s.supplier_name, count(*) AS po_count, sum(p.qty) AS open_qty
FROM open_po p JOIN suppliers s USING (supplier_id)
WHERE p.eta_date > (SELECT max(date) FROM sales_daily)
GROUP BY s.supplier_id, s.supplier_name
ORDER BY open_qty DESC
```""",
    ),
    FewShot(
        "哪些共用料被最多 SKU 使用？",
        """```sql
SELECT b.material_pn, m.material_name, count(DISTINCT b.sku_id) AS sku_count
FROM bom b JOIN materials m USING (material_pn)
GROUP BY b.material_pn, m.material_name
HAVING count(DISTINCT b.sku_id) > 1
ORDER BY sku_count DESC, b.material_pn
LIMIT 20
```""",
    ),
    FewShot(
        "当前红橙风险物料会影响哪些成品？列出前 20 个组合。",
        """```sql
SELECT r.material_pn, r.risk_level, b.sku_id, p.product_name, b.qty_per_unit
FROM material_risk r
JOIN bom b USING (material_pn)
JOIN products p USING (sku_id)
WHERE r.eval_date = (SELECT max(eval_date) FROM material_risk)
  AND r.risk_level IN ('RED', 'ORANGE')
ORDER BY CASE r.risk_level WHEN 'RED' THEN 1 ELSE 2 END, r.gap_qty DESC,
         r.material_pn, b.sku_id
LIMIT 20
```""",
    ),
    FewShot(
        "LT 覆盖率是什么意思？顺便看下当前覆盖率最低的 5 个物料。",
        """```sql
SELECT material_pn, lt_coverage, doi_days, risk_level
FROM material_risk
WHERE eval_date = (SELECT max(eval_date) FROM material_risk)
ORDER BY lt_coverage, material_pn
LIMIT 5
```""",
    ),
    FewShot("员工的工资和手机号是多少？", "NO_ANSWER"),
)

SQL_FENCE = re.compile(r"```sql\s*\n?(.*?)```", re.IGNORECASE | re.DOTALL)


def build_prompt(question: str) -> list[Message]:
    """Build system knowledge, eight executable examples, one refusal, and the question."""
    glossary = render_glossary(load_glossary())
    system = f"""You are ChainPilot's supply-chain NL-to-SQL compiler.
Convert an answerable user question into DuckDB SQL using only the documented schema.

{SCHEMA_CARDS}
Business glossary:
{glossary}

Output rules:
1. Return exactly one ```sql code block and no other text. Use DuckDB syntax and SELECT only.
2. Use only schema-card objects and columns. Prefer existing v_risk_* aggregate views.
3. For current/latest risk, always filter eval_date = (SELECT max(eval_date) FROM material_risk).
4. For current inventory, always use the latest snapshot_date (globally or per material as needed).
5. Never invent unavailable facts or columns. If the data cannot answer, return exactly NO_ANSWER.
6. Keep queries deterministic with explicit ordering where row order matters.
7. The data is a historical snapshot: treat (SELECT max(date) FROM sales_daily) as "today"
   whenever the question says 今天/当前/最近/未到货. Never use CURRENT_DATE, now() or today().
8. Put exactly ONE SELECT statement in the block; for multi-part summaries use UNION ALL,
   never multiple statements.
9. If the question mixes a terminology explanation with a data request, still generate SQL
   for the data part; the explanation is handled by a later step.
"""
    messages: list[Message] = [{"role": "system", "content": system}]
    for example in FEW_SHOTS:
        messages.append({"role": "user", "content": example.question})
        messages.append({"role": "assistant", "content": example.answer})
    messages.append({"role": "user", "content": question})
    return messages


def generate_sql(question: str, llm: ChatLLM) -> NL2SQLResult:
    """Ask the LLM and strictly classify its fenced-SQL response."""
    response = llm.chat(build_prompt(question), temperature=0.0, timeout=30)
    raw = response.content.strip(
        string.whitespace + string.punctuation + "，。！？；：、…（）【】《》“”‘’"
    )
    if raw == "NO_ANSWER":
        return NL2SQLResult("no_answer", None, response.content, response.usage)
    blocks = SQL_FENCE.findall(response.content)
    if not blocks:
        return NL2SQLResult("invalid_format", None, response.content, response.usage)
    if len(blocks) != 1:
        return NL2SQLResult("multiple_sql_blocks", None, response.content, response.usage)
    sql = blocks[0].strip()
    if not sql:
        return NL2SQLResult("invalid_format", None, response.content, response.usage)
    return NL2SQLResult("ok", sql, response.content, response.usage)


def _print_table(result: SafeResult) -> None:
    if not result.columns:
        print("(no rows)")
        return
    values = [[str(value) for value in row] for row in result.rows]
    widths = [len(column) for column in result.columns]
    for row in values:
        widths = [max(width, len(value)) for width, value in zip(widths, row, strict=True)]
    print(" | ".join(name.ljust(width) for name, width in zip(result.columns, widths, strict=True)))
    print("-+-".join("-" * width for width in widths))
    for row in values:
        print(" | ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)))


def main(argv: Sequence[str] | None = None) -> int:
    """Generate SQL, execute it through the safety layer, and show token cost."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(f'Usage: {sys.executable} api/agent/nl2sql.py "<question>"', file=sys.stderr)
        return 2
    result = generate_sql(args[0], DeepSeekClient())
    print(
        f"Tokens: prompt={result.usage.prompt_tokens}, "
        f"completion={result.usage.completion_tokens}, total={result.usage.total_tokens}"
    )
    if result.status != "ok" or result.sql is None:
        print(f"Status: {result.status}")
        return 0 if result.status == "no_answer" else 1
    print("SQL:\n" + result.sql)
    safe_result = execute_safe(result.sql)
    if not safe_result.ok:
        print(f"Rejected: {safe_result.rejected_reason}", file=sys.stderr)
        return 1
    print(f"\nRows ({safe_result.row_count}):")
    _print_table(safe_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
