"""Offline regression coverage for the M4-T1 NL-to-SQL foundation."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from agent.glossary import SCHEMA_CARDS, SCHEMA_OBJECTS, load_glossary, render_glossary
from agent.llm import DeepSeekClient, LLMResult, TokenUsage
from agent.nl2sql import FEW_SHOTS, SQL_FENCE, build_prompt, generate_sql
from agent.safe_sql import ALLOWED_TABLES, execute_safe

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"


class MockLLM:
    """Deterministic NL-to-SQL test double that records invocation options."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[tuple[object, float, float]] = []

    def chat(self, messages, *, temperature=0.0, timeout=30):
        self.calls.append((messages, temperature, timeout))
        return LLMResult(self.content, TokenUsage(12, 4))


def test_glossary_is_parsed_from_document() -> None:
    terms = load_glossary()
    assert len(terms) >= 10
    rendered = render_glossary(terms)
    assert "DOI (Days of Inventory)" in rendered
    assert "Share Split" in rendered


def test_glossary_missing_source_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_glossary(tmp_path / "missing.md")


def test_schema_cards_and_sql_whitelist_cover_identical_objects() -> None:
    assert SCHEMA_OBJECTS == ALLOWED_TABLES
    assert len(SCHEMA_OBJECTS) == 17
    for name in SCHEMA_OBJECTS:
        assert f"{name} --" in SCHEMA_CARDS


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("UPDATE materials SET material_name = 'x'", "not_select"),
        ("DELETE FROM materials", "not_select"),
        ("DROP TABLE materials", "not_select"),
        ("CREATE TABLE stolen (id INTEGER)", "not_select"),
        ("PRAGMA show_tables", "not_select"),
        ("ATTACH 'other.duckdb'", "not_select"),
        ("SELECT * FROM sample; SELECT * FROM sample", "multiple_statements"),
        ("SELECT * FROM information_schema.tables", "table_not_allowed"),
        (
            "SELECT * FROM sample WHERE id IN (SELECT table_oid FROM information_schema.tables)",
            "table_not_allowed",
        ),
        ("SELECT * FROM read_csv_auto('/tmp/secret.csv')", "table_not_allowed"),
    ],
)
def test_safe_sql_rejects_attack_surface(sql: str, reason: str) -> None:
    result = execute_safe(sql, allowed_tables={"sample"})
    assert not result.ok
    assert result.rejected_reason is not None
    assert result.rejected_reason.startswith(reason)
    assert result.rows == []


def test_safe_sql_executes_select_and_adds_limit() -> None:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("CREATE TABLE sample (id INTEGER, label VARCHAR)")
        connection.execute("INSERT INTO sample VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        result = execute_safe(
            "SELECT id, label FROM sample ORDER BY id",
            limit=2,
            connection=connection,
            allowed_tables={"sample"},
        )
    finally:
        connection.close()
    assert result.ok
    assert result.columns == ["id", "label"]
    assert result.rows == [(1, "a"), (2, "b")]
    assert result.row_count == 2
    assert result.final_sql is not None and "safe_query LIMIT 2" in result.final_sql


def test_safe_sql_respects_an_explicit_empty_whitelist() -> None:
    result = execute_safe("SELECT * FROM materials", allowed_tables=set())
    assert not result.ok
    assert result.rejected_reason == "table_not_allowed: materials"


def test_safe_sql_preserves_existing_top_level_limit() -> None:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("CREATE TABLE sample (id INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1), (2)")
        result = execute_safe(
            "SELECT id FROM sample ORDER BY id LIMIT 1",
            connection=connection,
            allowed_tables={"sample"},
        )
    finally:
        connection.close()
    assert result.ok
    assert result.rows == [(1,)]
    assert result.final_sql == "SELECT id FROM sample ORDER BY id LIMIT 1"


def test_safe_sql_allows_ctes_and_set_operations_over_whitelisted_tables() -> None:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("CREATE TABLE sample (id INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1), (2)")
        result = execute_safe(
            "WITH selected AS (SELECT id FROM sample WHERE id = 1) "
            "SELECT id FROM selected UNION ALL SELECT id FROM sample WHERE id = 2",
            connection=connection,
            allowed_tables={"sample"},
        )
    finally:
        connection.close()
    assert result.ok
    assert result.rows == [(1,), (2,)]


def test_safe_sql_uses_read_only_connection(tmp_path: Path) -> None:
    path = tmp_path / "test.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE sample (id INTEGER)")
    connection.execute("INSERT INTO sample VALUES (42)")
    connection.close()
    result = execute_safe("SELECT * FROM sample", db_path=path, allowed_tables={"sample"})
    assert result.ok
    assert result.rows == [(42,)]


def test_prompt_includes_schema_glossary_rules_and_few_shots() -> None:
    messages = build_prompt("查当前库存")
    system = messages[0]["content"]
    assert SCHEMA_CARDS in system
    assert "DOI (Days of Inventory)" in system
    assert "Share Split" in system
    assert system.count("- ") >= 10
    assert "latest snapshot_date" in system
    assert "eval_date = (SELECT max(eval_date) FROM material_risk)" in system
    assert len(FEW_SHOTS) == 10
    assert len(messages) == 2 * len(FEW_SHOTS) + 2


def test_generate_sql_extracts_single_fenced_block() -> None:
    llm = MockLLM("```sql\nSELECT material_pn FROM materials\n```")
    result = generate_sql("列出物料", llm)
    assert result.status == "ok"
    assert result.sql == "SELECT material_pn FROM materials"
    assert result.raw_response.startswith("```sql")
    assert result.usage == TokenUsage(12, 4)
    assert llm.calls[0][1:] == (0.0, 30)


@pytest.mark.parametrize(
    ("response", "status"),
    [
        ("NO_ANSWER", "no_answer"),
        ("SELECT * FROM materials", "invalid_format"),
        ("```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```", "multiple_sql_blocks"),
    ],
)
def test_generate_sql_classifies_non_sql_responses(response: str, status: str) -> None:
    result = generate_sql("问题", MockLLM(response))
    assert result.status == status
    assert result.sql is None


def test_deepseek_client_is_injectable_and_retries_once() -> None:
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("temporary")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="done"))],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
            )

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = DeepSeekClient(client=sdk, sleep=lambda _: None).chat([{"role": "user", "content": "x"}])
    assert result == LLMResult("done", TokenUsage(7, 2))
    assert len(calls) == 2
    assert all(call["model"] == "deepseek-v4-flash" for call in calls)
    assert all(call["temperature"] == 0.0 for call in calls)


def test_deepseek_client_uses_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    assert DeepSeekClient(client=sdk).model == "deepseek-v4-flash"


def test_deepseek_client_model_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-env-model")
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    assert DeepSeekClient(client=sdk).model == "deepseek-env-model"


def test_deepseek_client_constructor_model_overrides_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-env-model")
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    client = DeepSeekClient(client=sdk, model="deepseek-constructor-model")
    assert client.model == "deepseek-constructor-model"


@pytest.mark.skipif(not REAL_DB.exists(), reason="built ChainPilot database is absent")
def test_few_shot_sql_executes_against_real_database() -> None:
    sql_examples = [SQL_FENCE.findall(example.answer)[0].strip() for example in FEW_SHOTS[:-1]]
    assert len(sql_examples) == 9
    for sql in sql_examples:
        result = execute_safe(sql)
        assert result.ok, result.rejected_reason
        assert result.row_count >= 1, sql


@pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY is not configured"
)
@pytest.mark.parametrize(
    "question",
    [
        "哪些物料是红色风险？",
        "当前红橙风险物料影响哪些成品？",
        "按 commodity 汇总最新风险。",
    ],
)
def test_real_deepseek_smoke(question: str) -> None:
    generated = generate_sql(question, DeepSeekClient())
    assert generated.status == "ok"
    assert generated.sql is not None
    executed = execute_safe(generated.sql)
    assert executed.ok, executed.rejected_reason
    assert executed.row_count >= 1
