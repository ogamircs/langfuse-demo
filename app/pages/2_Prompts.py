"""Prompt management page: versions, labels, promotion, diff, live preview."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import difflib

import streamlit as st

from app.common import langfuse_banner
from agent.config import settings
from agent.prompts import PROMPT_VERSIONS, get_system_prompt, template_variables
from agent.tracing import get_langfuse, tracing_active

st.set_page_config(page_title="Prompts · Langfuse demo", page_icon="📝", layout="wide")
langfuse_banner()
st.title("📝 Prompt management")
st.markdown(
    "The agent's system prompt is **served from Langfuse** by label. Change the label in the chat sidebar, or "
    "promote a version here, and the next turn uses it — no redeploy. Every generation is linked to the prompt "
    "version that produced it, so you can compare cost/latency/scores per version on the Dashboard."
)

if not tracing_active():
    st.warning("Langfuse is not configured. The agent is using the local fallback prompt from `agent/context/system_prompt.md`.")
    st.code(get_system_prompt()[0][:3000])
    st.stop()

lf = get_langfuse()
name = settings.system_prompt_name

col_a, col_b = st.columns([3, 1])
with col_b:
    if st.button("Seed prompts into Langfuse", help="Creates 2 versions: v1 → production, v2 → experiment"):
        import scripts.seed_langfuse as seed  # noqa: WPS433
        seed.seed_prompts()
        lf.clear_prompt_cache()
        st.success("Seeded (see versions below).")
        st.rerun()
    if st.button("Clear SDK prompt cache"):
        lf.clear_prompt_cache()
        st.toast("cache cleared")

try:
    metas = lf.api.prompts.list(name=name).data
except Exception as exc:
    st.error(f"Could not list prompts: {exc}")
    st.stop()

if not metas:
    st.info(f"No prompt named `{name}` yet. Click **Seed prompts into Langfuse**. Local versions that will be created:")
    for i, v in enumerate(PROMPT_VERSIONS, 1):
        with st.expander(f"v{i} · labels {v['labels']} · config {v['config']}"):
            st.code(v["prompt"])
    st.stop()

meta = metas[0]
versions = sorted(meta.versions)
with col_a:
    st.markdown(f"**`{name}`** · versions {versions} · labels {meta.labels} · tags {meta.tags}")

fetched = {}
for v in versions:
    try:
        fetched[v] = lf.api.prompts.get(name, version=v)
    except Exception as exc:
        st.warning(f"v{v}: {exc}")

rows = [{"version": v, "labels": ", ".join(p.labels), "commit": p.commit_message, "config": p.config} for v, p in fetched.items()]
st.dataframe(rows, use_container_width=True, hide_index=True)

st.subheader("Promote a version to a label")
p1, p2, p3 = st.columns(3)
with p1:
    promote_v = st.selectbox("Version", versions, index=len(versions) - 1)
with p2:
    promote_label = st.selectbox("Label", ["production", "experiment", "staging"])
with p3:
    st.write("")
    if st.button(f"Set `{promote_label}` → v{promote_v}", type="primary"):
        lf.update_prompt(name=name, version=promote_v, new_labels=[promote_label])
        lf.clear_prompt_cache()
        st.success(f"v{promote_v} now serves label `{promote_label}`. The next chat turn picks it up.")
        st.rerun()

st.subheader("Diff two versions")
d1, d2 = st.columns(2)
va = d1.selectbox("From", versions, index=0, key="diff-a")
vb = d2.selectbox("To", versions, index=len(versions) - 1, key="diff-b")
if va in fetched and vb in fetched:
    diff = difflib.unified_diff(fetched[va].prompt.splitlines(), fetched[vb].prompt.splitlines(), f"v{va}", f"v{vb}", lineterm="")
    st.code("\n".join(diff) or "(identical)", language="diff")

st.subheader("Live preview of what the agent receives")
label = st.radio("Label", ["production", "experiment"], horizontal=True, key="preview-label")
compiled, client, pmeta = get_system_prompt(label)
st.caption(f"source: {pmeta['prompt_source']} · version: {pmeta['prompt_version']} · config: {pmeta['prompt_config']} · variables: {list(template_variables())}")
st.code(compiled[:4000])
