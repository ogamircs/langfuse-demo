"""Run one user turn through the Claude Agent SDK with full Langfuse tracing."""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from agent.config import settings
from agent.prompts import get_system_prompt
from agent.tools import ALLOWED_TOOLS, build_mcp_servers
from agent.tracing import TurnTrace, build_hooks, record_evaluator, trace_url, turn_trace

ROOT = Path(__file__).resolve().parents[1]

SUBAGENTS = {
    "sql_analyst": AgentDefinition(
        description="Runs deep multi-query SQL investigations (5+ queries) and returns a compact numeric summary. "
        "Use it for open-ended 'find the drivers of X' questions; do not use it for single-query questions.",
        prompt="You are a SQL analyst. Call describe_schema once, then answer the delegated question with as few "
        "aggregate queries as possible. Return only the key numbers and the SQL you used. Never output e-mails or card numbers.",
        tools=["mcp__data__describe_schema", "mcp__data__run_sql", "mcp__analysis__calculate"],
        model="sonnet",
    )
}


@dataclass
class AgentTurnResult:
    answer: str
    trace_id: str | None
    trace_url: str | None
    sdk_session_id: str | None
    summary: dict[str, Any]
    events: list[dict] = field(default_factory=list)
    prompt_meta: dict[str, Any] = field(default_factory=dict)
    sql_statements: list[str] = field(default_factory=list)


def _heuristic_scores(answer: str) -> dict[str, float | bool]:
    return {
        "cites_numbers": bool(re.search(r"\d", answer)),
        "shows_sql": "```" in answer and bool(re.search(r"\bselect\b", answer, re.IGNORECASE)),
        "pii_leak": bool(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\bLC-\d{6,}", answer)),
        "answer_chars": float(len(answer)),
    }


async def run_agent(
    prompt: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    sdk_session_id: str | None = None,
    prompt_label: str = "production",
    effort: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    on_text: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
    include_external_mcp: bool = True,
    max_turns: int | None = None,
) -> AgentTurnResult:
    """Execute one turn. `sdk_session_id` resumes a previous Claude Agent SDK session (multi-turn chat)."""
    user_id = user_id or settings.default_user
    session_id = session_id or str(uuid.uuid4())
    system_prompt, prompt_client, prompt_meta = get_system_prompt(prompt_label)
    effort = effort or prompt_meta.get("prompt_config", {}).get("effort") or settings.effort
    turn_tags = ["claude-agent-sdk", f"prompt:{prompt_label}", f"effort:{effort}", *(tags or [])]
    turn_meta = {"model": settings.model, "effort": effort, "prompt_label": prompt_label,
                 "prompt_version": prompt_meta.get("prompt_version"), "prompt_source": prompt_meta.get("prompt_source"),
                 "resumed": bool(sdk_session_id), **(metadata or {})}

    answer_parts: list[str] = []
    result: ResultMessage | None = None
    turn: TurnTrace | None = None

    with turn_trace(prompt=prompt, user_id=user_id, session_id=session_id, tags=turn_tags, metadata=turn_meta,
                    prompt_client=prompt_client) as turn:
        options = ClaudeAgentOptions(
            model=settings.model,
            system_prompt=system_prompt,
            tools=["Agent"],  # no built-in file/bash tools; MCP tools are added via mcp_servers
            allowed_tools=ALLOWED_TOOLS,
            # "default" + allowed_tools auto-approves exactly our MCP tools and works headless as any
            # user ("bypassPermissions" is refused when running as root, e.g. in containers).
            permission_mode=settings.permission_mode,  # type: ignore[arg-type]
            mcp_servers=build_mcp_servers(include_external=include_external_mcp),
            hooks=build_hooks(turn, on_event),
            agents=SUBAGENTS,
            max_turns=max_turns or settings.max_turns,
            max_budget_usd=settings.max_budget_usd,
            include_partial_messages=True,
            thinking={"type": "adaptive"},
            effort=effort,  # type: ignore[arg-type]
            resume=sdk_session_id,
            setting_sources=[],
            cwd=str(ROOT),
        )
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, StreamEvent):
                if message.parent_tool_use_id:  # subagent text is traced but not streamed to the UI
                    continue
                turn.on_stream_event(message.event)
                if on_text and message.event.get("type") == "content_block_delta":
                    delta = message.event.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        on_text(delta["text"])
            elif isinstance(message, AssistantMessage):
                if message.parent_tool_use_id:
                    continue
                turn.on_assistant_message(message)
                if any(isinstance(b, ToolUseBlock) for b in message.content):
                    answer_parts.clear()
                    if on_text:
                        on_text("\n")
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        answer_parts.append(block.text)
            elif isinstance(message, SystemMessage):
                if message.subtype == "init":
                    turn.event("init", mcp_servers=[s.get("name") for s in (message.data.get("mcp_servers") or [])],
                               tools=len(message.data.get("tools") or []))
            elif isinstance(message, ResultMessage):
                result = message

        answer = "\n".join(p for p in answer_parts if p).strip() or (result.result if result and result.result else "")
        summary = turn.on_result(result, answer) if result else {"is_error": True}
        record_evaluator(turn, name="heuristic-quality", input={"answer_preview": answer[:300]},
                         scores=_heuristic_scores(answer), comment="in-process heuristic evaluator")

    return AgentTurnResult(
        answer=answer,
        trace_id=turn.trace_id if turn else None,
        trace_url=trace_url(turn.trace_id) if turn else None,
        sdk_session_id=summary.get("sdk_session_id") if summary else None,
        summary=summary,
        events=list(turn.events) if turn else [],
        prompt_meta=prompt_meta,
        sql_statements=list(turn.sql_statements) if turn else [],
    )


def run_agent_sync(prompt: str, **kwargs: Any) -> AgentTurnResult:
    """Blocking wrapper for Streamlit callbacks, scripts and experiments."""
    return asyncio.run(run_agent(prompt, **kwargs))
