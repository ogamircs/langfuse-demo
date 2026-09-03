"""Langfuse glue for the Claude Agent SDK.

This module is the heart of the demo. It turns the Agent SDK's event stream and hooks
into a Langfuse observation tree:

    trace (user_id / session_id / tags / prompt version)
    └── agent  "loyalty-agent-turn"              <- one per user turn (root observation)
        ├── generation  "claude-sonnet-5"        <- one per model call (tokens, cost, latency, TTFT)
        ├── tool  "mcp__data__run_sql"           <- opened by PreToolUse hook, closed by PostToolUse
        │   └── tool  "run_sql.impl"             <- emitted from inside the tool implementation
        ├── agent  "subagent:sql_analyst"        <- SubagentStart/SubagentStop hooks
        ├── guardrail  "pii-output-check"        <- custom guardrail observation
        └── evaluator  "heuristic-quality"       <- cheap in-process evaluator + scores

Everything degrades gracefully when Langfuse is not configured: the SDK is created with
tracing disabled and all helpers become no-ops.
"""
from __future__ import annotations

import contextvars
import functools
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from langfuse import Langfuse, propagate_attributes

from agent.config import settings

try:  # imported lazily by type checkers; the SDK is always installed for the demo
    from claude_agent_sdk import HookMatcher
except Exception:  # pragma: no cover
    HookMatcher = None  # type: ignore

# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------
_client_lock = threading.Lock()
_client: Langfuse | None = None

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CARD_RE = re.compile(r"\bLC-\d{6,10}\b")


def mask_pii(*, data: Any, **_: Any) -> Any:
    """Langfuse `mask` callback: redact e-mails and loyalty card numbers before export."""
    if isinstance(data, str):
        return _CARD_RE.sub("LC-********", _EMAIL_RE.sub("[email redacted]", data))
    if isinstance(data, dict):
        return {k: mask_pii(data=v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return type(data)(mask_pii(data=v) for v in data)
    return data


_BLOCKED_SCOPES = {"mcp-python-sdk"}


def _should_export_span(span: Any) -> bool:
    from langfuse.span_filter import is_default_export_span

    scope = getattr(span, "instrumentation_scope", None)
    return is_default_export_span(span) and (scope is None or scope.name not in _BLOCKED_SCOPES)


def get_langfuse() -> Langfuse:
    """Process-wide Langfuse client (created once, safe to call from Streamlit reruns)."""
    global _client
    with _client_lock:
        if _client is None:
            enabled = settings.tracing_enabled and settings.langfuse_configured
            # Placeholder keys when unconfigured keep the SDK quiet; tracing_enabled=False means
            # nothing is ever exported and every helper in this module short-circuits.
            _client = Langfuse(
                public_key=settings.langfuse_public_key or "pk-lf-not-configured",
                secret_key=settings.langfuse_secret_key or "sk-lf-not-configured",
                base_url=settings.langfuse_base_url,
                environment=settings.langfuse_environment,
                release=settings.langfuse_release,
                tracing_enabled=enabled,
                mask=mask_pii if settings.mask_pii else None,
                # The mcp package emits its own protocol-level OTel spans ("tools/call x");
                # drop them so the tree reads: tool (hook) -> tool.impl.
                should_export_span=_should_export_span,
            )
        return _client


def reset_client_for_tests() -> None:
    """Drop the process-wide client AND Langfuse's per-public-key singleton registry.

    The SDK caches one resource manager (exporter thread etc.) per public key; after a
    shutdown that cached instance must not be reused, or the next flush blocks forever.
    """
    global _client
    with _client_lock:
        try:
            from langfuse._client.resource_manager import LangfuseResourceManager

            LangfuseResourceManager.reset()
        except Exception:
            if _client is not None:
                try:
                    _client.shutdown()
                except Exception:
                    pass
        _client = None


def tracing_active() -> bool:
    return settings.tracing_enabled and settings.langfuse_configured


# --------------------------------------------------------------------------------------
# Cost estimation (USD per 1M tokens: input, output, cache read, cache write).
# Langfuse can also price generations itself from its model table; we send explicit
# cost_details so the dashboard is exact even for models Langfuse has not catalogued yet.
# --------------------------------------------------------------------------------------
PRICES_PER_MTOK: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3, 3.75),
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.5, 6.25),
    "claude-haiku-4-5": (1.0, 5.0, 0.1, 1.25),
}


def estimate_cost(model: str | None, usage: dict[str, int]) -> dict[str, float] | None:
    if not usage:
        return None
    key = next((k for k in PRICES_PER_MTOK if model and model.startswith(k)), None)
    if key is None:
        return None
    p_in, p_out, p_cr, p_cw = PRICES_PER_MTOK[key]
    cost = {
        "input": usage.get("input", 0) * p_in / 1e6,
        "output": usage.get("output", 0) * p_out / 1e6,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) * p_cr / 1e6,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) * p_cw / 1e6,
    }
    cost["total"] = sum(cost.values())
    return cost


# --------------------------------------------------------------------------------------
# Per-turn trace state
# --------------------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class TurnTrace:
    """Mutable state for one user turn; shared by the runner, hooks and tool wrappers."""

    root: Any  # LangfuseAgent observation wrapper
    trace_id: str
    user_id: str
    session_id: str
    events: list[dict] = field(default_factory=list)  # UI timeline
    tool_spans: dict[str, Any] = field(default_factory=dict)  # tool_use_id -> observation
    tool_names: dict[str, str] = field(default_factory=dict)  # tool_use_id -> tool name
    subagent_spans: dict[str, Any] = field(default_factory=dict)  # agent_id -> observation
    open_generation: Any = None
    generation_first_token: datetime | None = None
    generation_count: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    estimated_cost_usd: float = 0.0
    sql_statements: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # ---- helpers -----------------------------------------------------------------
    def trace_context(self, parent: Any = None) -> dict[str, str]:
        parent = parent or self.root
        return {"trace_id": self.trace_id, "parent_span_id": parent.id}

    def _parent_for(self, agent_id: str | None) -> Any:
        if agent_id and agent_id in self.subagent_spans:
            return self.subagent_spans[agent_id]
        return self.root

    def event(self, kind: str, **payload: Any) -> None:
        self.events.append({"ts": _now_iso(), "kind": kind, **payload})

    # ---- generations (driven by the runner) ----------------------------------------
    def on_stream_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "message_start":
            self._open_generation(event.get("message", {}).get("model"))
        elif etype == "content_block_delta" and self.open_generation is not None and self.generation_first_token is None:
            self.generation_first_token = datetime.now(timezone.utc)
            try:
                self.open_generation.update(completion_start_time=self.generation_first_token)
            except Exception:
                pass

    def _open_generation(self, model: str | None) -> None:
        if self.open_generation is not None:
            return
        self.generation_count += 1
        self.generation_first_token = None
        self.open_generation = get_langfuse().start_observation(
            trace_context=self.trace_context(),
            as_type="generation",
            name=model or settings.model,
            model=model or settings.model,
            model_parameters={"effort": settings.effort, "thinking": "adaptive", "max_turns": settings.max_turns},
            metadata={"turn_index": self.generation_count},
        )

    def on_assistant_message(self, message: Any) -> None:
        """Close (or create+close) the generation observation for an AssistantMessage."""
        model = getattr(message, "model", None) or settings.model
        if self.open_generation is None:
            self._open_generation(model)
        usage = usage_details_from_anthropic(getattr(message, "usage", None))
        cost = estimate_cost(model, usage)
        if cost:
            self.estimated_cost_usd += cost["total"]
        gen = self.open_generation
        try:
            gen.update(
                model=model,
                output=serialize_content(getattr(message, "content", [])),
                usage_details=usage or None,
                cost_details=cost,
                metadata={"message_id": getattr(message, "message_id", None), "stop_reason": getattr(message, "stop_reason", None)},
                level="ERROR" if getattr(message, "error", None) else None,
                status_message=str(getattr(message, "error", "")) or None,
            )
        finally:
            gen.end()
        self.open_generation = None
        self.event("generation", model=model, usage=usage, output=serialize_content(getattr(message, "content", [])))

    def on_result(self, result: Any, answer_text: str) -> dict[str, Any]:
        """Finalize the root observation from the SDK ResultMessage. Returns a summary for the UI."""
        usage = usage_details_from_anthropic(getattr(result, "usage", None))
        model_usage = getattr(result, "model_usage", None) or {}
        cost = getattr(result, "total_cost_usd", None)
        summary = {
            "cost_usd": cost if cost is not None else (self.estimated_cost_usd or None),
            "cost_estimated_from_generations_usd": round(self.estimated_cost_usd, 6),
            "usage": usage,
            "duration_ms": getattr(result, "duration_ms", None),
            "duration_api_ms": getattr(result, "duration_api_ms", None),
            "num_turns": getattr(result, "num_turns", None),
            "is_error": getattr(result, "is_error", False),
            "sdk_session_id": getattr(result, "session_id", None),
            "generations": self.generation_count,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "stop_reason": getattr(result, "stop_reason", None),
        }
        try:
            # usage/cost live on the generation observations (Langfuse aggregates them per trace);
            # the root keeps the SDK's authoritative totals in metadata for the dashboard.
            self.root.update(
                output=answer_text,
                metadata={**summary, "model_usage": model_usage},
                level="ERROR" if summary["is_error"] else None,
                status_message="; ".join(getattr(result, "errors", None) or []) or None,
            )
        except Exception:
            pass
        # Close anything a hook left open (e.g. the run was interrupted).
        for span in list(self.tool_spans.values()) + list(self.subagent_spans.values()):
            try:
                span.update(level="WARNING", status_message="closed at end of turn")
                span.end()
            except Exception:
                pass
        self.tool_spans.clear()
        self.subagent_spans.clear()
        if self.open_generation is not None:
            self.open_generation.end()
            self.open_generation = None
        self.event("result", **summary)
        return summary


# The active turn is stored in a ContextVar so tool implementations (which the SDK runs as
# asyncio tasks inside the same process) can attach their own observations to it.
_current_turn: contextvars.ContextVar[TurnTrace | None] = contextvars.ContextVar("langfuse_turn", default=None)
_last_turn: TurnTrace | None = None


def current_turn() -> TurnTrace | None:
    return _current_turn.get() or _last_turn


class turn_trace:
    """Context manager used by the runner: opens the trace + root `agent` observation."""

    def __init__(self, *, prompt: str, user_id: str, session_id: str, tags: list[str] | None = None,
                 metadata: dict[str, Any] | None = None, prompt_client: Any = None, name: str = "loyalty-agent-turn"):
        self.prompt = prompt
        self.user_id = user_id
        self.session_id = session_id
        self.tags = tags or []
        self.metadata = metadata or {}
        self.prompt_client = prompt_client
        self.name = name
        self._stack: list[Any] = []
        self.turn: TurnTrace | None = None
        self._token = None

    def __enter__(self) -> TurnTrace:
        global _last_turn
        lf = get_langfuse()
        propagate = propagate_attributes(
            user_id=self.user_id,
            session_id=self.session_id,
            tags=self.tags,
            metadata={k: str(v) for k, v in self.metadata.items()},
            trace_name=self.name,
            prompt=self.prompt_client,
        )
        propagate.__enter__()
        self._stack.append(propagate)
        root_cm = lf.start_as_current_observation(as_type="agent", name=self.name, input=self.prompt,
                                                  metadata=self.metadata, prompt=self.prompt_client)
        root = root_cm.__enter__()
        self._stack.append(root_cm)
        self.turn = TurnTrace(root=root, trace_id=root.trace_id, user_id=self.user_id, session_id=self.session_id)
        self._token = _current_turn.set(self.turn)
        _last_turn = self.turn
        return self.turn

    def __exit__(self, exc_type, exc, tb) -> bool:
        global _last_turn
        if exc is not None and self.turn is not None:
            try:
                self.turn.root.update(level="ERROR", status_message=f"{exc_type.__name__}: {exc}")
            except Exception:
                pass
        while self._stack:
            cm = self._stack.pop()
            try:
                cm.__exit__(exc_type, exc, tb)
            except Exception:
                pass
        if self._token is not None:
            _current_turn.reset(self._token)
        if _last_turn is self.turn:
            _last_turn = None
        try:
            get_langfuse().flush()
        except Exception:
            pass
        return False


# --------------------------------------------------------------------------------------
# Claude Agent SDK hooks -> Langfuse observations
# --------------------------------------------------------------------------------------
def build_hooks(turn: TurnTrace, on_event: Callable[[dict], None] | None = None) -> dict[str, list]:
    """Return a `ClaudeAgentOptions.hooks` mapping bound to this turn."""
    lf = get_langfuse()

    def emit(kind: str, **payload: Any) -> None:
        turn.event(kind, **payload)
        if on_event:
            try:
                on_event(turn.events[-1])
            except Exception:
                pass

    async def pre_tool_use(input_data: dict, tool_use_id: str | None, _ctx: Any) -> dict:
        tool_name = input_data.get("tool_name", "tool")
        tool_input = input_data.get("tool_input", {})
        key = tool_use_id or input_data.get("tool_use_id") or f"{tool_name}-{turn.tool_calls}"
        parent = turn._parent_for(input_data.get("agent_id"))
        turn.tool_calls += 1
        try:
            span = lf.start_observation(
                trace_context=turn.trace_context(parent),
                as_type="tool",
                name=tool_name,
                input=tool_input,
                metadata={"tool_use_id": key, "agent_id": input_data.get("agent_id"), "mcp_server": _mcp_server(tool_name)},
            )
            turn.tool_spans[key] = span
            turn.tool_names[key] = tool_name
        except Exception:
            pass
        if tool_name.endswith("run_sql") and isinstance(tool_input, dict) and tool_input.get("sql"):
            turn.sql_statements.append(str(tool_input["sql"]))
        emit("tool_start", tool=tool_name, tool_use_id=key, input=tool_input)
        return {}

    async def post_tool_use(input_data: dict, tool_use_id: str | None, _ctx: Any) -> dict:
        key = tool_use_id or input_data.get("tool_use_id")
        span = turn.tool_spans.pop(key, None)
        response = input_data.get("tool_response")
        text = tool_response_text(response)
        is_error = _looks_like_error(response)
        if is_error:
            turn.tool_errors += 1
        if span is not None:
            try:
                span.update(output=text, level="ERROR" if is_error else None,
                            status_message="tool returned is_error" if is_error else None)
            finally:
                span.end()
        emit("tool_end", tool=input_data.get("tool_name"), tool_use_id=key, output=text[:4000], is_error=is_error)
        return {}

    async def post_tool_use_failure(input_data: dict, tool_use_id: str | None, _ctx: Any) -> dict:
        key = tool_use_id or input_data.get("tool_use_id")
        span = turn.tool_spans.pop(key, None)
        turn.tool_errors += 1
        err = str(input_data.get("error", "tool failed"))
        if span is not None:
            try:
                span.update(output=err, level="ERROR", status_message=err[:200])
            finally:
                span.end()
        emit("tool_error", tool=input_data.get("tool_name"), tool_use_id=key, error=err)
        return {}

    async def subagent_start(input_data: dict, _tid: str | None, _ctx: Any) -> dict:
        agent_id = input_data.get("agent_id", "subagent")
        try:
            turn.subagent_spans[agent_id] = lf.start_observation(
                trace_context=turn.trace_context(),
                as_type="agent",
                name=f"subagent:{input_data.get('agent_type', 'unknown')}",
                metadata={"agent_id": agent_id},
            )
        except Exception:
            pass
        emit("subagent_start", agent_id=agent_id, agent_type=input_data.get("agent_type"))
        return {}

    async def subagent_stop(input_data: dict, _tid: str | None, _ctx: Any) -> dict:
        agent_id = input_data.get("agent_id", "subagent")
        span = turn.subagent_spans.pop(agent_id, None)
        if span is not None:
            span.update(output="completed").end()
        emit("subagent_stop", agent_id=agent_id)
        return {}

    async def user_prompt_submit(input_data: dict, _tid: str | None, _ctx: Any) -> dict:
        emit("prompt", prompt=input_data.get("prompt"))
        return {}

    async def stop(input_data: dict, _tid: str | None, _ctx: Any) -> dict:
        emit("stop")
        return {}

    if HookMatcher is None:  # pragma: no cover
        return {}
    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
        "PostToolUseFailure": [HookMatcher(hooks=[post_tool_use_failure])],
        "SubagentStart": [HookMatcher(hooks=[subagent_start])],
        "SubagentStop": [HookMatcher(hooks=[subagent_stop])],
        "UserPromptSubmit": [HookMatcher(hooks=[user_prompt_submit])],
        "Stop": [HookMatcher(hooks=[stop])],
    }


def _mcp_server(tool_name: str) -> str | None:
    parts = tool_name.split("__")
    return parts[1] if len(parts) >= 3 and parts[0] == "mcp" else None


def _looks_like_error(response: Any) -> bool:
    if isinstance(response, dict):
        return bool(response.get("is_error") or response.get("isError"))
    return False


def tool_response_text(response: Any) -> str:
    """Flatten an MCP tool response into text for span output / UI display."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list):
            return "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
        return json.dumps(response, default=str)
    if isinstance(response, list):
        return "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in response)
    return str(response)


# --------------------------------------------------------------------------------------
# Observations emitted from inside tool implementations
# --------------------------------------------------------------------------------------
def traced_tool(name: str, as_type: str = "tool"):
    """Decorator for tool implementations: adds an `<name>.impl` observation under the hook span."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(args: dict[str, Any]) -> dict[str, Any]:
            turn = current_turn()
            if turn is None or not tracing_active():
                return await fn(args)
            parent = None
            for key, tool_name in reversed(list(turn.tool_names.items())):
                if tool_name.endswith(f"__{name}") and key in turn.tool_spans:
                    parent = turn.tool_spans[key]
                    break
            obs = get_langfuse().start_observation(
                trace_context=turn.trace_context(parent), as_type=as_type, name=f"{name}.impl", input=args
            )
            try:
                result = await fn(args)
                text = tool_response_text(result)
                obs.update(output=text[:20000], level="ERROR" if result.get("is_error") else None)
                return result
            except Exception as exc:
                obs.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                obs.end()

        return wrapper

    return decorator


def record_guardrail(turn: TurnTrace | None, *, name: str, passed: bool, input: Any, details: str) -> None:
    if turn is None or not tracing_active():
        return
    obs = get_langfuse().start_observation(
        trace_context=turn.trace_context(), as_type="guardrail", name=name, input=input,
        output={"passed": passed, "details": details}, level=None if passed else "WARNING",
        status_message=None if passed else details[:200],
    )
    obs.end()
    turn.event("guardrail", name=name, passed=passed, details=details)


def record_evaluator(turn: TurnTrace | None, *, name: str, input: Any, scores: dict[str, float | str | bool], comment: str = "") -> None:
    """Emit an `evaluator` observation and attach its scores to the trace."""
    if turn is None or not tracing_active():
        return
    lf = get_langfuse()
    obs = lf.start_observation(trace_context=turn.trace_context(), as_type="evaluator", name=name, input=input, output=scores)
    obs.end()
    for score_name, value in scores.items():
        data_type = "BOOLEAN" if isinstance(value, bool) else "CATEGORICAL" if isinstance(value, str) else "NUMERIC"
        lf.create_score(trace_id=turn.trace_id, observation_id=obs.id, name=score_name,
                        value=float(value) if isinstance(value, bool) else value, data_type=data_type, comment=comment or None)
    turn.event("evaluator", name=name, scores=scores)


# --------------------------------------------------------------------------------------
# Scores (human feedback from the UI)
# --------------------------------------------------------------------------------------
def score_trace(trace_id: str, *, name: str, value: float | str | bool, comment: str | None = None,
                data_type: str | None = None, session_id: str | None = None) -> None:
    if not tracing_active():
        return
    if data_type is None:
        data_type = "BOOLEAN" if isinstance(value, bool) else "CATEGORICAL" if isinstance(value, str) else "NUMERIC"
    lf = get_langfuse()
    lf.create_score(trace_id=trace_id, name=name, value=float(value) if isinstance(value, bool) else value,
                    data_type=data_type, comment=comment)
    lf.flush()


def trace_url(trace_id: str) -> str | None:
    if not tracing_active():
        return None
    try:
        return get_langfuse().get_trace_url(trace_id=trace_id)
    except Exception:
        return None


# --------------------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------------------
def usage_details_from_anthropic(usage: Any) -> dict[str, int]:
    """Map Anthropic usage (dict or object) to Langfuse usage_details keys."""
    if not usage:
        return {}
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    out: dict[str, int] = {}
    mapping = {
        "input_tokens": "input",
        "output_tokens": "output",
        "cache_read_input_tokens": "cache_read_input_tokens",
        "cache_creation_input_tokens": "cache_creation_input_tokens",
    }
    for src, dst in mapping.items():
        v = get(src)
        if isinstance(v, (int, float)):
            out[dst] = int(v)
    if "input" in out or "output" in out:
        out["total"] = out.get("input", 0) + out.get("output", 0) + out.get("cache_read_input_tokens", 0) + out.get("cache_creation_input_tokens", 0)
    return out


def serialize_content(blocks: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in blocks or []:
        t = type(b).__name__
        if t == "TextBlock":
            out.append({"type": "text", "text": getattr(b, "text", "")})
        elif t == "ToolUseBlock":
            out.append({"type": "tool_use", "id": getattr(b, "id", None), "name": getattr(b, "name", None), "input": getattr(b, "input", None)})
        elif t == "ToolResultBlock":
            out.append({"type": "tool_result", "tool_use_id": getattr(b, "tool_use_id", None), "content": getattr(b, "content", None), "is_error": getattr(b, "is_error", None)})
        elif t == "ThinkingBlock":
            out.append({"type": "thinking", "thinking": (getattr(b, "thinking", "") or "")[:2000]})
        elif isinstance(b, dict):
            out.append(b)
        else:
            out.append({"type": t, "repr": str(b)[:500]})
    return out
