"""Run the Dashboard page against canned Langfuse API responses served by the local stub."""
import importlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.otlp_stub import OtlpStub

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-03T12:00:00.000Z"


def _canned() -> dict:
    trace = {"id": "t1", "timestamp": NOW, "name": "loyalty-agent-turn", "input": "How many members?", "output": "1,500",
             "sessionId": "s1", "userId": "analyst.amir", "metadata": {"cost_usd": 0.01, "prompt_label": "production", "tool_calls": 2},
             "tags": ["ui"], "public": False, "environment": "development", "htmlPath": "/project/p/traces/t1",
             "latency": 7.4, "totalCost": 0.0123, "observations": ["o1"], "scores": ["sc1"]}
    obs = {"id": "o1", "traceId": "t1", "type": "TOOL", "name": "mcp__data__run_sql", "startTime": NOW, "endTime": NOW,
           "modelParameters": None, "input": None, "metadata": None, "output": None, "usage": None, "level": "ERROR",
           "statusMessage": "boom", "usageDetails": None, "costDetails": None, "environment": "development", "latency": 0.02}
    score = {"id": "sc1", "dataType": "NUMERIC", "value": 4, "projectId": "p", "name": "accuracy", "source": "API", "timestamp": NOW,
             "environment": "development", "createdAt": NOW, "updatedAt": NOW, "subject": {"kind": "trace", "id": "t1"}}
    cat = {**score, "id": "sc2", "dataType": "CATEGORICAL", "value": 1, "stringValue": "too-verbose", "name": "issue-category"}
    meta = {"page": 1, "limit": 50, "totalItems": 1, "totalPages": 1}
    return {
        "/api/public/v2/metrics": {"data": [{"count_count": 5, "totalCost_sum": 0.5, "totalTokens_sum": 1000, "latency_p50": 5000,
                                             "latency_p95": 9000, "value_avg": 0.8, "value_count": 4, "time_dimension": NOW,
                                             "providedModelName": "claude-sonnet-5", "name": "mcp__data__run_sql", "promptVersion": 1}]},
        "/api/public/traces": {"data": [trace], "meta": meta},
        "/api/public/v2/observations": {"data": [obs], "meta": meta},
        "/api/public/v3/scores": {"data": [score, cat], "meta": meta},
        "/api/public/sessions": {"data": [{"id": "s1", "createdAt": NOW, "projectId": "p", "environment": "development"}], "meta": meta},
    }


@pytest.fixture()
def live_stub(monkeypatch):
    stub = OtlpStub().start()
    for path, payload in _canned().items():
        stub.serve(path, payload)
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", stub.url)
    import agent.config as config
    import agent.tracing as tracing
    importlib.reload(config)
    importlib.reload(tracing)
    tracing.reset_client_for_tests()
    yield stub
    tracing.reset_client_for_tests()
    stub.stop()
    importlib.reload(config)
    importlib.reload(tracing)


def test_dashboard_with_canned_api(live_stub):
    import streamlit as st

    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "app/pages/1_Dashboard.py"), default_timeout=90)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Agent turns"] == "5"
    assert metrics["LLM cost"] == "$0.5000"
    assert metrics["Latency p50"] == "5.0s"
    assert metrics["Tool error rate"] == "100.0%"
    assert metrics["👍 rate"] == "80%"
    deltas = {m.label: m.delta for m in at.metric}
    assert deltas["Latency p50"] == "p95 9.0s" and deltas["LLM cost"] == "$0.1000 per turn"
    assert any("/api/public/v2/metrics" in r for r in live_stub.requests)
    assert any("/api/public/traces" in r for r in live_stub.requests)
    assert not any("_lf_last_error" in str(c.value) and "error" in str(c.value) for c in at.caption)
    assert not at.warning, [w.value for w in at.warning]


def test_prompts_and_evals_pages_with_stub(live_stub):
    live_stub.serve("/api/public/v2/prompts", {"data": [], "meta": {"page": 1, "limit": 50, "totalItems": 0, "totalPages": 0}})
    for page in ("app/pages/2_Prompts.py", "app/pages/3_Evals.py"):
        at = AppTest.from_file(str(ROOT / page), default_timeout=90)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
