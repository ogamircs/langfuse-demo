"""`reasoning` MCP server: planning scaffold, self-critique and a per-session scratch memory.

These tools give the trace visible "reasoning" steps that Langfuse can show, and give the
model a structured place to plan and check its work.
"""
from __future__ import annotations

import re
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from agent.tracing import current_turn, traced_tool

_MEMORY: dict[str, dict[str, str]] = {}

_PLAYBOOK_HINTS = [
    (r"lift|campaign|promotion", "Use the playbook: campaign window = start..end inclusive; baseline = 28 days before start; normalise per day; report members in each window."),
    (r"laps|churn|at risk|risk", "Lapse-risk = last transaction 45–89 days old (segment-definitions doc); lapsed at 90, churned at 180 days."),
    (r"active member|active in", "Active member = at least one transaction in the calendar month (loyalty-program-rules)."),
    (r"private.label|compliments", "Private-label share = revenue from is_private_label products / total revenue (kpi-glossary)."),
    (r"basket", "Basket size = revenue per distinct txn_id (kpi-glossary)."),
    (r"square foot|sqft|per sq", "Revenue per square foot = store revenue / stores.sqft (kpi-glossary)."),
    (r"forecast|next month|predict", "Pull a monthly series with run_sql, then call naive_forecast; state the method used."),
    (r"e-?mail|loyalty card|phone|address", "Privacy rule: never output e-mails or card numbers; refer to members by customer_id."),
]


def _text(text: str, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["is_error"] = True
    return out


def _session_key() -> str:
    turn = current_turn()
    return turn.session_id if turn else "default"


@tool(
    "plan_steps",
    "Turn an analytical goal into a numbered execution plan with the relevant business rules attached. "
    "Call this first for multi-step questions, then execute the steps.",
    {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]},
)
@traced_tool("plan_steps", as_type="chain")
async def plan_steps(args: dict[str, Any]) -> dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    hints = [h for pat, h in _PLAYBOOK_HINTS if re.search(pat, goal, re.IGNORECASE)]
    steps = [
        "Confirm definitions (search_docs) for any business term in the goal.",
        "Inspect the schema (describe_schema) and identify the tables/joins needed.",
        "Write one aggregate SQL per metric (run_sql); keep row counts small.",
        "Do all arithmetic with calculate / compare_periods.",
        "Draft the answer, then call critique before responding.",
    ]
    plan = f"**Goal:** {goal}\n\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
    if hints:
        plan += "\n\n**Applicable rules:**\n" + "\n".join(f"- {h}" for h in hints)
    return _text(plan)


@tool(
    "critique",
    "Self-check a draft answer against the question: flags missing numbers, missing SQL, PII leakage, unstated baselines and vague language.",
    {"type": "object", "properties": {"question": {"type": "string"}, "draft": {"type": "string"}}, "required": ["question", "draft"]},
)
@traced_tool("critique", as_type="evaluator")
async def critique(args: dict[str, Any]) -> dict[str, Any]:
    q, d = str(args.get("question", "")), str(args.get("draft", ""))
    issues: list[str] = []
    d_no_pii = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\bLC-\d{6,}", "", d)
    if not re.search(r"\d", d_no_pii):
        issues.append("The draft contains no numbers; analytical answers should lead with the figure.")
    if "```" not in d and re.search(r"revenue|members|customers|share|lift|how many|which", q, re.IGNORECASE):
        issues.append("No SQL shown; include the query you ran in a fenced code block.")
    if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\bLC-\d{6,}", d):
        issues.append("PII detected (e-mail or loyalty card) — remove it and refer to customer_id only.")
    if re.search(r"lift|campaign", q, re.IGNORECASE) and not re.search(r"baseline|28 days|prior|before", d, re.IGNORECASE):
        issues.append("Campaign lift must state the baseline window explicitly.")
    if re.search(r"\b(approximately|roughly|around|about)\b", d, re.IGNORECASE) and re.search(r"exact|how many|total", q, re.IGNORECASE):
        issues.append("The question asks for an exact figure; avoid hedging words when the SQL result is exact.")
    if len(d) > 2500:
        issues.append("Answer is long; tighten to finding → explanation → SQL.")
    verdict = "PASS — no issues found." if not issues else "REVISE:\n" + "\n".join(f"- {i}" for i in issues)
    return _text(verdict)


@tool("remember", "Store a note for later in this session (e.g. an intermediate number or an assumption).",
      {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]})
@traced_tool("remember")
async def remember(args: dict[str, Any]) -> dict[str, Any]:
    mem = _MEMORY.setdefault(_session_key(), {})
    mem[str(args["key"])] = str(args["value"])
    return _text(f"Stored '{args['key']}'. Session now holds {len(mem)} note(s).")


@tool("recall", "Retrieve notes stored with `remember` in this session. Omit key to list everything.",
      {"type": "object", "properties": {"key": {"type": "string"}}})
@traced_tool("recall")
async def recall(args: dict[str, Any]) -> dict[str, Any]:
    mem = _MEMORY.get(_session_key(), {})
    key = args.get("key")
    if key:
        return _text(mem.get(str(key), f"No note named '{key}'."))
    if not mem:
        return _text("No notes stored in this session.")
    return _text("\n".join(f"- **{k}**: {v}" for k, v in mem.items()))


SERVER = create_sdk_mcp_server(name="reasoning", version="1.0.0", tools=[plan_steps, critique, remember, recall])
