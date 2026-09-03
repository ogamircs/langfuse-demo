"""Datasets & experiments page: seed the golden dataset and run it as a Langfuse experiment."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json

import streamlit as st

from app.common import langfuse_banner
from agent.config import settings
from agent.evals import build_dataset_items, run_experiment, seed_dataset
from agent.tracing import get_langfuse, tracing_active

st.set_page_config(page_title="Evals · Langfuse demo", page_icon="🧪", layout="wide")
langfuse_banner()
st.title("🧪 Datasets & experiments")
st.markdown(
    "The golden dataset holds the sample questions with ground truth computed from the seeded warehouse. "
    "**Run experiment** sends every item through the agent; Langfuse records one traced run per item, attaches "
    "the evaluator scores, and lets you compare runs (e.g. `production` vs `experiment` prompt) side by side."
)

items = build_dataset_items()
with st.expander(f"Dataset `{settings.dataset_name}` — {len(items)} items (local definition)", expanded=False):
    st.dataframe(
        [{"id": i["id"], "category": i["metadata"]["category"], "question": i["input"]["question"],
          "expected": i["expected_output"].get("value"),
          "facts": json.dumps(i["expected_output"].get("facts") or {}, default=str)} for i in items],
        use_container_width=True, hide_index=True,
    )

st.subheader("Evaluators")
st.markdown(
    "- `numeric_match` — golden number appears in the answer (±1%)\n"
    "- `mentions_facts` — expected names (banner, segment, city…) mentioned\n"
    "- `shows_sql`, `no_pii`, `answer_chars` — format and safety checks\n"
    "- `llm_judge` *(optional)* — Claude Sonnet grades helpfulness 0–1 using the `loyalty-agent-judge` prompt from Langfuse\n"
    "- run-level: `avg_cost_usd`, `total_cost_usd`, `numeric_pass_rate`"
)

if not tracing_active():
    st.warning("Langfuse is not configured — experiments can still run locally but nothing will be recorded.")

c1, c2, c3, c4 = st.columns(4)
label = c1.selectbox("Prompt label", ["production", "experiment"])
limit = c2.number_input("Items (0 = all)", 0, len(items), 4)
judge = c3.toggle("Add LLM judge", value=False)
if c4.button("Seed dataset in Langfuse", disabled=not tracing_active()):
    n = seed_dataset()
    st.success(f"dataset `{settings.dataset_name}` upserted with {n} items")

if st.button("▶ Run experiment", type="primary"):
    with st.status("Running experiment… (each item is a full agent turn)", expanded=True) as status:
        result = run_experiment(prompt_label=label, limit=int(limit) or None, use_llm_judge=judge, max_concurrency=2)
        status.update(label="Experiment finished", state="complete")
    st.session_state["last_experiment"] = result

result = st.session_state.get("last_experiment")
if result is not None:
    st.subheader(f"Result: {result.run_name}")
    if result.dataset_run_url:
        st.link_button("Open dataset run in Langfuse ↗", result.dataset_run_url)
    rows = []
    for ir in result.item_results:
        out = ir.output if isinstance(ir.output, dict) else {"answer": str(ir.output)}
        row = {"question": (ir.item.input.get("question") if hasattr(ir.item, "input") and isinstance(ir.item.input, dict)
                            else (ir.item.get("input", {}).get("question") if isinstance(ir.item, dict) else "")),
               "cost_usd": out.get("cost_usd"), "tool_calls": out.get("tool_calls"), "trace_id": ir.trace_id,
               "answer": (out.get("answer") or "")[:200]}
        for ev in ir.evaluations:
            row[ev.name] = ev.value if isinstance(ev.value, (int, float)) else str(ev.value)
        rows.append(row)
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.markdown("**Run-level evaluations**")
    st.dataframe([{"name": e.name, "value": str(e.value)} for e in result.run_evaluations], hide_index=True)
    with st.expander("Formatted summary"):
        st.code(result.format())

if tracing_active():
    st.subheader("Previous runs")
    try:
        runs = get_langfuse().api.datasets.get_runs(settings.dataset_name).data
    except Exception as exc:
        runs = []
        st.caption("No experiment runs found for this dataset yet. Seed the dataset and run an experiment above.")
        with st.expander("API detail"):
            st.code(str(exc)[:1500])
    if runs:
        st.dataframe([{"run": r.name, "created": r.created_at, "description": r.description,
                       "metadata": json.dumps(r.metadata, default=str)} for r in runs],
                     use_container_width=True, hide_index=True)
