"""Streamlit chat UI for the Loyalty Insights Agent (Claude Agent SDK + Langfuse)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uuid

import streamlit as st

from app.common import DEMO_USERS, fmt_int, fmt_ms, fmt_usd, langfuse_banner
from agent.config import settings
from agent.context.sample_questions import SAMPLE_QUESTIONS
from agent.runner import run_agent_sync
from agent.tracing import score_trace, tracing_active

st.set_page_config(page_title="Loyalty Insights Agent · Langfuse demo", page_icon="🛒", layout="wide")

# ---------------------------------------------------------------------------------------
# Session bootstrap: one Streamlit session == one Langfuse session == one SDK conversation
# ---------------------------------------------------------------------------------------
ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("langfuse_session_id", f"chat-{uuid.uuid4().hex[:12]}")
ss.setdefault("sdk_session_id", None)
ss.setdefault("pending_prompt", None)


def new_conversation() -> None:
    ss.messages = []
    ss.langfuse_session_id = f"chat-{uuid.uuid4().hex[:12]}"
    ss.sdk_session_id = None


# ---------------------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------------------
with st.sidebar:
    st.title("🛒 Loyalty Insights Agent")
    langfuse_banner()
    st.divider()
    user_id = st.selectbox("User (Langfuse `user_id`)", DEMO_USERS, index=0, help="Every trace is attributed to this user; filter by it in Langfuse.")
    prompt_label = st.radio("System prompt label", ["production", "experiment"], horizontal=True,
                            help="Served from Langfuse prompt management; fall back to the local prompt if unreachable.")
    effort = st.select_slider("Effort", ["low", "medium", "high"], value=settings.effort)
    include_external = st.toggle("External stdio MCP server (utils)", value=True)
    st.caption(f"Langfuse session: `{ss.langfuse_session_id}`")
    if ss.sdk_session_id:
        st.caption(f"Claude SDK session: `{ss.sdk_session_id[:8]}…` (resumed each turn)")
    st.button("🆕 New conversation", on_click=new_conversation, use_container_width=True)
    st.divider()
    st.markdown("**Try a sample question**")
    for i, q in enumerate(SAMPLE_QUESTIONS):
        if st.button(q["question"], key=f"sample-{i}", use_container_width=True, help=f"category: {q['category']}"):
            ss.pending_prompt = q["question"]

# ---------------------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------------------
TOOL_ICON = {"tool_start": "🔧", "tool_end": "✅", "tool_error": "❌", "subagent_start": "🤖", "subagent_stop": "🏁",
             "generation": "💬", "guardrail": "🛡️", "evaluator": "📏", "result": "🧾", "init": "⚙️", "prompt": "📝", "stop": "⏹️"}


def render_timeline(events: list[dict]) -> None:
    for e in events:
        kind = e["kind"]
        icon = TOOL_ICON.get(kind, "•")
        if kind == "tool_start":
            with st.expander(f"{icon} `{e['tool']}`", expanded=False):
                st.json(e.get("input") or {})
        elif kind == "tool_end":
            with st.expander(f"{icon} `{e['tool']}` → result{' (error)' if e.get('is_error') else ''}", expanded=False):
                st.markdown(e.get("output") or "_empty_")
        elif kind == "tool_error":
            st.error(f"{icon} `{e['tool']}` failed: {e.get('error')}")
        elif kind == "generation":
            u = e.get("usage") or {}
            st.caption(f"{icon} generation · {e.get('model')} · in {fmt_int(u.get('input'))} / out {fmt_int(u.get('output'))} / cache-read {fmt_int(u.get('cache_read_input_tokens'))}")
        elif kind == "guardrail":
            (st.success if e.get("passed") else st.warning)(f"{icon} guardrail `{e['name']}`: {e.get('details')}")
        elif kind == "evaluator":
            st.caption(f"{icon} evaluator `{e['name']}`: {e.get('scores')}")
        elif kind in ("subagent_start", "subagent_stop"):
            st.caption(f"{icon} subagent {e.get('agent_type') or ''} {e.get('agent_id')}")
        elif kind == "init":
            st.caption(f"{icon} MCP servers: {', '.join(e.get('mcp_servers') or [])} · {e.get('tools')} tools")


def render_assistant(msg: dict, idx: int) -> None:
    st.markdown(msg["content"] or "_(no answer)_")
    summary = msg.get("summary") or {}
    meta = msg.get("prompt_meta") or {}
    cols = st.columns(5)
    cols[0].metric("Cost", fmt_usd(summary.get("cost_usd")))
    cols[1].metric("Latency", fmt_ms(summary.get("duration_ms")))
    cols[2].metric("Tokens", fmt_int((summary.get("usage") or {}).get("total")))
    cols[3].metric("Model turns", fmt_int(summary.get("num_turns")))
    cols[4].metric("Tool calls", f"{summary.get('tool_calls', 0)} ({summary.get('tool_errors', 0)} err)")
    version = meta.get("prompt_version")
    st.caption(
        f"System prompt `{meta.get('prompt_label', '?')}` · "
        + (f"Langfuse v{version}" if version else f"{meta.get('prompt_source', 'local')} prompt")
        + f" · effort `{(msg.get('effort') or settings.effort)}` · model `{settings.model}`"
    )
    with st.expander("🔍 Trace timeline (what Langfuse sees)", expanded=False):
        if msg.get("trace_url"):
            st.link_button("Open trace in Langfuse ↗", msg["trace_url"])
        elif msg.get("trace_id") and tracing_active():
            st.caption(f"trace id `{msg['trace_id']}`")
        elif not tracing_active():
            st.caption("Langfuse not configured: this timeline is the local view of the events that would be traced.")
        render_timeline(msg.get("events") or [])
    if msg.get("trace_id") and tracing_active():
        fb1, fb2, fb3 = st.columns([1, 2, 3])
        with fb1:
            thumbs = st.feedback("thumbs", key=f"thumbs-{idx}")
            if thumbs is not None and ss.get(f"thumbs-sent-{idx}") != thumbs:
                score_trace(msg["trace_id"], name="user-feedback", value=bool(thumbs), comment=f"thumbs {'up' if thumbs else 'down'} from {msg.get('user_id')}")
                ss[f"thumbs-sent-{idx}"] = thumbs
                st.toast("Sent BOOLEAN score `user-feedback` to Langfuse")
        with fb2:
            rating = st.slider("Accuracy (1-5)", 1, 5, 4, key=f"rating-{idx}")
            issue = st.selectbox("Issue", ["none", "wrong-number", "wrong-sql", "too-verbose", "missed-definition", "pii"], key=f"issue-{idx}")
        with fb3:
            comment = st.text_input("Comment", key=f"comment-{idx}", placeholder="optional reviewer note")
            if st.button("Submit rating", key=f"rate-{idx}"):
                score_trace(msg["trace_id"], name="accuracy", value=float(rating), comment=comment or None)
                score_trace(msg["trace_id"], name="issue-category", value=issue, data_type="CATEGORICAL", comment=comment or None)
                st.toast("Sent NUMERIC `accuracy` + CATEGORICAL `issue-category` scores")


# ---------------------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------------------
st.markdown("### Ask about members, campaigns and revenue — every turn is traced in Langfuse")
for i, msg in enumerate(ss.messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render_assistant(msg, i)
        else:
            st.markdown(msg["content"])

prompt = st.chat_input("e.g. Which segment has the highest average basket size?")
if ss.pending_prompt and not prompt:
    prompt, ss.pending_prompt = ss.pending_prompt, None

if prompt:
    ss.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        status = st.status("Agent is working…", expanded=True)
        placeholder = st.empty()
        buffer: list[str] = []

        def on_text(chunk: str) -> None:
            buffer.append(chunk)
            placeholder.markdown("".join(buffer) + "▌")

        def on_event(e: dict) -> None:
            k = e["kind"]
            if k == "tool_start":
                status.write(f"🔧 `{e['tool']}` {str(e.get('input'))[:160]}")
            elif k == "tool_error":
                status.write(f"❌ `{e['tool']}`: {e.get('error')}")
            elif k == "subagent_start":
                status.write(f"🤖 subagent `{e.get('agent_type')}` started")
            elif k == "guardrail":
                status.write(f"🛡️ guardrail `{e['name']}` {'passed' if e.get('passed') else 'flagged'}: {e.get('details')}")

        try:
            result = run_agent_sync(
                prompt, user_id=user_id, session_id=ss.langfuse_session_id, sdk_session_id=ss.sdk_session_id,
                prompt_label=prompt_label, effort=effort, on_text=on_text, on_event=on_event,
                include_external_mcp=include_external, tags=["ui"], metadata={"surface": "streamlit"},
            )
            status.update(label="Done", state="complete", expanded=False)
            placeholder.empty()
            ss.sdk_session_id = result.sdk_session_id or ss.sdk_session_id
            ss.messages.append({
                "role": "assistant", "content": result.answer, "summary": result.summary, "events": result.events,
                "trace_id": result.trace_id, "trace_url": result.trace_url, "prompt_meta": result.prompt_meta, "user_id": user_id,
                "effort": effort,
            })
        except Exception as exc:  # surfaced in UI; the trace root is marked ERROR by turn_trace
            status.update(label="Failed", state="error")
            st.exception(exc)
            ss.messages.append({"role": "assistant", "content": f"**Error:** {exc}", "summary": {}, "events": [], "trace_id": None})
    st.rerun()
