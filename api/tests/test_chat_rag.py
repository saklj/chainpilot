"""Offline production-RAG assembly and graceful-fallback tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from agent.chat import answer_question
from agent.llm import LLMResult, TokenUsage
from agent.nl2sql import FEW_SHOTS, FewShot
from agent.retrieval import build_index, few_shots_for
from app.deps import get_few_shots_provider, get_llm
from app.main import app

REAL_DB = Path(__file__).resolve().parents[2] / "data" / "chainpilot.duckdb"


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(sum(map(ord, text)) % 97), float(len(text)), 1.0] for text in texts]


class SequenceLLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, *, temperature=0.0, timeout=30):
        self.calls.append(messages)
        return LLMResult(self.responses.pop(0), TokenUsage(8, 2))


@pytest.fixture
def db_copy(tmp_path: Path) -> Path:
    if not REAL_DB.is_file():
        pytest.skip("real DuckDB fixture is unavailable")
    path = tmp_path / "chainpilot.duckdb"
    shutil.copyfile(REAL_DB, path)
    return path


def test_retrieval_fallback_and_hybrid_injection(db_copy: Path, caplog) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        connection.execute("DROP TABLE IF EXISTS qa_embedding")
        with caplog.at_level("WARNING"):
            assert few_shots_for(connection, FakeEmbedder(), "风险问题") == FEW_SHOTS
        assert "using fixed examples" in caplog.text

        assert build_index(connection, FakeEmbedder()) == 50
        shots = few_shots_for(connection, FakeEmbedder(), "风险问题", k=4)
        assert len(shots) == 5
        assert shots[-1].answer == "NO_ANSWER"
        assert sum(shot.answer == "NO_ANSWER" for shot in shots) >= 1
    finally:
        connection.close()


def test_chat_route_provider_and_fallback(db_copy: Path) -> None:
    del db_copy
    injected = (FewShot("注入示例一", "```sql\nSELECT 1\n```"), FewShot("越界", "NO_ANSWER"))
    llm = SequenceLLM("```sql\nSELECT 1 AS value\n```", "结果是 1。")
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_few_shots_provider] = lambda: (lambda question: injected)
    try:
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"question": "测试问题"})
        assert response.status_code == 200
        assert response.json()["answer"] == "结果是 1。"
        assert any(item["content"] == "注入示例一" for item in llm.calls[0])
    finally:
        app.dependency_overrides.clear()

    fallback_llm = SequenceLLM("NO_ANSWER")
    app.dependency_overrides[get_llm] = lambda: fallback_llm
    app.dependency_overrides[get_few_shots_provider] = lambda: (
        lambda question: (_ for _ in ()).throw(RuntimeError("retrieval offline"))
    )
    try:
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"question": "越界问题"})
        assert response.status_code == 200
        assert response.json()["refused"] is True
        assert any(item["content"] == FEW_SHOTS[0].question for item in fallback_llm.calls[0])
    finally:
        app.dependency_overrides.clear()


def test_answer_question_default_behavior_is_unchanged(db_copy: Path) -> None:
    del db_copy
    left = SequenceLLM("NO_ANSWER")
    right = SequenceLLM("NO_ANSWER")
    assert answer_question("无法回答的问题", left) == answer_question(
        "无法回答的问题", right, few_shots=None
    )
