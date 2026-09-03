"""Verify the Langfuse span tree the demo would send, using a local OTLP capture stub.

No LLM and no network: hooks are driven with fake payloads and the tool implementations are
invoked directly, then the exported spans are decoded and their structure asserted.
"""
import asyncio
import importlib
import json
import os

import pytest

from tests.otlp_stub import OtlpStub


@pytest.fixture()
def langfuse_env(monkeypatch):
    stub = OtlpStub().start()
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", stub.url)
    monkeypatch.setenv("LANGFUSE_FLUSH_INTERVAL", "0.2")
    import agent.config as config
    importlib.reload(config)
    import agent.tracing as tracing
    importlib.reload(tracing)
    tracing.reset_client_for_tests()
    yield stub, tracing
    tracing.get_langfuse().flush()
    tracing.reset_client_for_tests()
    stub.stop()
    importlib.reload(config)
    importlib.reload(tracing)


class FakeText:
    def __init__(self, text):
        self.text = text


FakeText.__name__ = "TextBlock"


class FakeToolUse:
    def __init__(self, name, inp):
        self.id, self.name, self.input = "toolu_1", name, inp


FakeToolUse.__name__ = "ToolUseBlock"


class FakeAssistant:
    def __init__(self, content, usage):
        self.content, self.usage, self.model = content, usage, "claude-sonnet-5"
        self.message_id, self.stop_reason, self.error = "msg_1", "end_turn", None


class FakeResult:
    def __init__(self):
        self.usage = {"input_tokens": 120, "output_tokens": 40, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}
        self.model_usage = {"claude-sonnet-5": {"inputTokens": 120, "outputTokens": 40, "costUSD": 0.0123}}
        self.total_cost_usd, self.duration_ms, self.duration_api_ms = 0.0123, 1500, 1200
        self.num_turns, self.is_error, self.session_id, self.stop_reason, self.errors = 3, False, "sdk-sess-1", "end_turn", None


def test_span_tree_and_attributes(langfuse_env):
    stub, tracing = langfuse_env
    assert tracing.tracing_active()

    async def scenario():
        with tracing.turn_trace(prompt="How many members?", user_id="u-1", session_id="s-1",
                                tags=["demo"], metadata={"prompt_label": "production"}) as turn:
            hooks = tracing.build_hooks(turn)
            pre, post, fail = hooks["PreToolUse"][0].hooks[0], hooks["PostToolUse"][0].hooks[0], hooks["PostToolUseFailure"][0].hooks[0]
            sub_start, sub_stop = hooks["SubagentStart"][0].hooks[0], hooks["SubagentStop"][0].hooks[0]

            # generation 1: model decides to call a tool (streamed)
            turn.on_stream_event({"type": "message_start", "message": {"model": "claude-sonnet-5"}})
            turn.on_stream_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Let me"}})
            turn.on_assistant_message(FakeAssistant([FakeText("Let me check."), FakeToolUse("mcp__data__run_sql", {"sql": "SELECT COUNT(*) FROM customers"})],
                                                    {"input_tokens": 100, "output_tokens": 20}))
            # tool call via hooks, with the real tool implementation in between
            await pre({"hook_event_name": "PreToolUse", "tool_name": "mcp__data__run_sql", "tool_input": {"sql": "SELECT COUNT(*) FROM customers"}}, "toolu_1", None)
            from agent.tools.sql_tools import run_sql
            res = await run_sql.handler({"sql": "SELECT customer_id, email FROM customers LIMIT 2"})  # triggers PII guardrail
            await post({"hook_event_name": "PostToolUse", "tool_name": "mcp__data__run_sql", "tool_input": {"sql": "SELECT 1"}, "tool_response": res}, "toolu_1", None)
            # a failing tool
            await pre({"hook_event_name": "PreToolUse", "tool_name": "mcp__analysis__calculate", "tool_input": {"expression": "x"}}, "toolu_2", None)
            await fail({"hook_event_name": "PostToolUseFailure", "tool_name": "mcp__analysis__calculate", "tool_input": {}, "error": "boom"}, "toolu_2", None)
            # subagent with a nested tool call
            await sub_start({"hook_event_name": "SubagentStart", "agent_id": "ag-1", "agent_type": "sql_analyst"}, None, None)
            await pre({"hook_event_name": "PreToolUse", "tool_name": "mcp__data__describe_schema", "tool_input": {}, "agent_id": "ag-1"}, "toolu_3", None)
            await post({"hook_event_name": "PostToolUse", "tool_name": "mcp__data__describe_schema", "tool_input": {}, "tool_response": {"content": [{"type": "text", "text": "schema"}]}, "agent_id": "ag-1"}, "toolu_3", None)
            await sub_stop({"hook_event_name": "SubagentStop", "agent_id": "ag-1", "agent_type": "sql_analyst"}, None, None)
            # final generation
            turn.on_assistant_message(FakeAssistant([FakeText("There are 1,500 members. Contact member1@example.com")], {"input_tokens": 200, "output_tokens": 30}))
            summary = turn.on_result(FakeResult(), "There are 1,500 members.")
            tracing.record_evaluator(turn, name="heuristic-quality", input={}, scores={"cites_numbers": True, "answer_chars": 24.0})
            return turn, summary

    turn, summary = asyncio.run(scenario())
    tracing.get_langfuse().flush()
    assert summary["cost_usd"] == 0.0123 and summary["tool_calls"] == 3 and summary["tool_errors"] == 1

    by_type = stub.by_type()
    assert set(by_type) >= {"agent", "generation", "tool", "guardrail", "evaluator"}, by_type.keys()
    roots = [s for s in by_type["agent"] if s["name"] == "loyalty-agent-turn"]
    assert len(roots) == 1
    root = roots[0]
    assert all(s["trace_id"] == root["trace_id"] for s in stub.spans)

    attrs = root["attributes"]
    assert attrs.get("user.id") == "u-1" and attrs.get("session.id") == "s-1"
    assert "demo" in (attrs.get("langfuse.trace.tags") or [])
    assert attrs.get("langfuse.observation.metadata.cost_usd") in (0.0123, "0.0123")
    assert attrs.get("langfuse.observation.metadata.num_turns") in (3, "3")

    gens = by_type["generation"]
    assert len(gens) == 2 and all(g["parent_span_id"] == root["span_id"] for g in gens)
    assert json.loads(gens[0]["attributes"]["langfuse.observation.usage_details"])["output"] == 20
    assert json.loads(gens[0]["attributes"]["langfuse.observation.cost_details"])["total"] == pytest.approx(100 * 2 / 1e6 + 20 * 10 / 1e6)
    assert gens[0]["attributes"]["langfuse.observation.model.name"] == "claude-sonnet-5"
    assert gens[0]["attributes"].get("langfuse.observation.completion_start_time")
    # PII masking applied at export time
    assert "member1@example.com" not in json.dumps(gens[1]["attributes"])

    tools = {t["name"]: t for t in by_type["tool"]}
    assert tools["mcp__data__run_sql"]["parent_span_id"] == root["span_id"]
    assert tools["run_sql.impl"]["parent_span_id"] == tools["mcp__data__run_sql"]["span_id"]
    assert tools["mcp__analysis__calculate"]["attributes"]["langfuse.observation.level"] == "ERROR"
    sub = [s for s in by_type["agent"] if s["name"] == "subagent:sql_analyst"][0]
    assert sub["parent_span_id"] == root["span_id"]
    assert tools["mcp__data__describe_schema"]["parent_span_id"] == sub["span_id"]
    assert by_type["guardrail"][0]["name"] == "pii-output-check"
    assert by_type["evaluator"][0]["name"] == "heuristic-quality"
    # every span ended
    assert all(s["end"] > s["start"] for s in stub.spans)


def test_noop_when_unconfigured(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    import agent.config as config, agent.tracing as tracing
    importlib.reload(config); importlib.reload(tracing)
    tracing.reset_client_for_tests()
    with tracing.turn_trace(prompt="hi", user_id="u", session_id="s") as turn:
        turn.on_assistant_message(FakeAssistant([FakeText("ok")], {}))
        turn.on_result(FakeResult(), "ok")
    tracing.score_trace(turn.trace_id, name="thumbs", value=True)  # no error, no network
    assert turn.events[-1]["kind"] == "result"


def test_mask_pii():
    import agent.tracing as tracing
    out = tracing.mask_pii(data={"a": "mail me at joe@x.com card LC-12345678", "b": ["LC-9999999"]})
    assert out == {"a": "mail me at [email redacted] card LC-********", "b": ["LC-********"]}
