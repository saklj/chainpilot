"""Offline tests for injectable few-shot embedding retrieval."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import duckdb
import pytest

from agent.nl2sql import build_prompt
from agent.retrieval import BgeEmbedder, RetrievedExample, build_index, retrieve_examples

REAL_DB = Path(__file__).resolve().parents[2] / "data" / "chainpilot.duckdb"


class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vectors[text] for text in texts]


def _questions(tmp_path: Path) -> Path:
    path = tmp_path / "questions.jsonl"
    rows = [
        {"id": "Q1", "category": "risk", "question": "红色风险", "gold_sql": "SELECT 1"},
        {"id": "Q2", "category": "risk", "question": "橙色风险", "gold_sql": "SELECT 2"},
        {"id": "Q3", "category": "po", "question": "在途采购", "gold_sql": "SELECT 3"},
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    return path


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_build_retrieve_leave_one_out_and_threshold(tmp_path: Path) -> None:
    vectors = {
        "红色风险": [1.0, 0.0],
        "橙色风险": [0.8, 0.2],
        "在途采购": [0.0, 1.0],
        "查询风险": [1.0, 0.0],
    }
    db_copy = tmp_path / "chainpilot.duckdb"
    shutil.copyfile(REAL_DB, db_copy)
    connection = duckdb.connect(str(db_copy))
    try:
        path = _questions(tmp_path)
        embedder = FakeEmbedder(vectors)
        assert build_index(connection, embedder, path) == 3
        assert build_index(connection, embedder, path) == 3
        assert connection.execute("SELECT count(*), count(DISTINCT question_id) FROM qa_embedding").fetchone() == (3, 3)

        result = retrieve_examples(connection, embedder, "查询风险", k=3)
        assert [item.question_id for item in result] == ["Q1", "Q2", "Q3"]
        excluded = retrieve_examples(connection, embedder, "红色风险", k=3, exclude_id="Q1")
        assert all(item.question_id != "Q1" for item in excluded)
        filtered = retrieve_examples(connection, embedder, "查询风险", k=3, min_similarity=0.99)
        assert [item.question_id for item in filtered] == ["Q1"]

        prompt = build_prompt("动态问题", [item.to_few_shot() for item in result[:2]])
        assert prompt[-1] == {"role": "user", "content": "动态问题"}
        assert "SELECT 1" in prompt[2]["content"]
    finally:
        connection.close()


def test_default_prompt_is_byte_identical_and_fastembed_is_lazy() -> None:
    encoded = json.dumps(
        build_prompt("回归问题"), ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == (
        "fab423716cead74e19bb37833e9e2487d4c888e130040ccc39019f84b46e1e3d"
    )
    assert RetrievedExample("A01", "越权问题", "", "adversarial", 0.9).to_few_shot().answer == "NO_ANSWER"
    BgeEmbedder()
