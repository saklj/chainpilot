"""Offline SSE contract tests for the diagnosis API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.llm import LLMResult, TokenUsage
from app.deps import get_llm
from app.main import app

REAL_DB = Path(__file__).resolve().parents[2] / "data" / "chainpilot.duckdb"


class SequenceLLM:
    def __init__(self, *responses: str, error: Exception | None = None) -> None:
        self.responses = list(responses)
        self.error = error

    def chat(self, messages, *, temperature=0.0, timeout=30):
        del messages, temperature, timeout
        if self.error is not None:
            raise self.error
        return LLMResult(self.responses.pop(0), TokenUsage(8, 2))


def _events(response) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_diagnose_stream_steps_and_result() -> None:
    app.dependency_overrides[get_llm] = lambda: SequenceLLM(
        '{"action":"get_risk_detail","args":{"material_pn":"PN-00211"}}',
        '{"action":"get_po_status","args":{"material_pn":"PN-00211"}}',
        '{"action":"final","category":"single_source_supply","root_cause":"该料只有 1 家供应源，库存 20，在途 9，缺口 434。"}',
    )
    try:
        with TestClient(app) as client:
            response = client.post("/api/diagnose/stream", json={"material_pn": "PN-00211"})
        events = _events(response)
        assert [event["type"] for event in events] == ["step", "step", "result"]
        assert events[-1]["category"] == "single_source_supply"
        assert events[-1]["steps"] == 3
        assert events[-1]["guardrail"] == "pass"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_diagnose_stream_404_and_failures_close() -> None:
    app.dependency_overrides[get_llm] = lambda: SequenceLLM("bad", "still bad")
    try:
        with TestClient(app) as client:
            missing = client.post("/api/diagnose/stream", json={"material_pn": "PN-NOT-FOUND"})
            degraded = client.post("/api/diagnose/stream", json={"material_pn": "PN-00211"})
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "material_not_found"
        events = _events(degraded)
        assert [event["type"] for event in events] == ["retry", "result"]
        assert events[-1]["degraded"] is True
    finally:
        app.dependency_overrides.clear()

    app.dependency_overrides[get_llm] = lambda: SequenceLLM(error=RuntimeError("offline"))
    try:
        with TestClient(app) as client:
            response = client.post("/api/diagnose/stream", json={"material_pn": "PN-00211"})
        assert _events(response) == [{"type": "error", "message": "offline"}]
    finally:
        app.dependency_overrides.clear()
