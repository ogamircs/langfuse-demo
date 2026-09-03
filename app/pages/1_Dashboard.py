"""Monitoring dashboard fed by the Langfuse public API (Metrics API + traces/observations/scores)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app import langfuse_data as lfd  # noqa: E402
from app.common import fmt_ms, fmt_usd, langfuse_banner  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.tracing import tracing_active  # noqa: E402

st.set_page_config(page_title="Dashboard · Langfuse demo", page_icon="📊", layout="wide")
langfuse_banner()
st.title("📊 Agent monitoring (from Langfuse)")

# Reference categorical palette (fixed slot order, validated CVD-safe) + neutral tokens.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b"}
GRID, INK2 = "#e6e5e1", "#52514e"

if not tracing_active():
    st.warning("Langfuse is not configured, so there is nothing to query yet. Set the keys in `.env`, chat a few turns "
               "(or run `make traffic`) and come back.")
    st.stop()

# ---------------------------------------------------------------------------------------
# Filters (one row)
# ---------------------------------------------------------------------------------------
f1, f2, f3, f4, f5 = st.columns([1.2, 1.2, 1.4, 1.2, 0.8])
range_label = f1.selectbox("Time range", ["Last 24 hours", "Last 7 days", "Last 30 days"], index=1)
days = {"Last 24 hours": 1, "Last 7 days": 7, "Last 30 days": 30}[range_label]
env = f2.text_input("Environment", value=settings.langfuse_environment, help="blank = all environments") or None
user_filter = f3.text_input("User id (tables)", value="", placeholder="e.g. analyst.amir") or None
tag_filter = f4.text_input("Tag (tables)", value="", placeholder="e.g. ui, simulated") or None
if f5.button("↻ Refresh", use_container_width=True):
    lfd.clear_cache()
    st.rerun()

ROOT_FILTER = [{"column": "name", "operator": "=", "value": "loyalty-agent-turn", "type": "string"}]
GEN_FILTER = [{"column": "type", "operator": "=", "value": "GENERATION", "type": "string"}]
TOOL_FILTER = [{"column": "type", "operator": "=", "value": "TOOL", "type": "string"}]
ERR_FILTER = [{"column": "level", "operator": "=", "value": "ERROR", "type": "string"}]


def scalar(df: pd.DataFrame, measure: str, agg: str):
    col = lfd.value_col(df, measure, agg)
    if df.empty or col is None:
        return None
    try:
        return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum() if agg in ("sum", "count") else pd.to_numeric(df[col], errors="coerce").mean())
    except Exception:
        return None


def base_layout(fig: go.Figure, title: str, y_title: str = "", height: int = 300) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=14), x=0), height=height, margin=dict(l=8, r=8, t=44, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color=INK2, size=12),
        legend=dict(orientation="h", y=-0.2, x=0, title=None), hovermode="x unified", bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=GRID)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, title=y_title, rangemode="tozero")
    return fig


def time_axis(fig: go.Figure) -> go.Figure:
    """Date ticks for time-bucketed charts (avoids sub-second ticks when only one bucket exists)."""
    fig.update_xaxes(type="date", tickformat="%b %d" if gran == "day" else "%b %d %H:%M", nticks=8)
    return fig


# ---------------------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------------------
turns = lfd.metrics("observations", [("count", "count")], [], ROOT_FILTER, None, days, env)
latency = lfd.metrics("observations", [("latency", "p50"), ("latency", "p95")], [], ROOT_FILTER, None, days, env)
gens = lfd.metrics("observations", [("count", "count"), ("totalCost", "sum"), ("totalTokens", "sum")], [], GEN_FILTER, None, days, env)
tools = lfd.metrics("observations", [("count", "count")], [], TOOL_FILTER, None, days, env)
tool_errors = lfd.metrics("observations", [("count", "count")], [], TOOL_FILTER + ERR_FILTER, None, days, env)
feedback = lfd.metrics("scores-boolean", [("value", "avg"), ("count", "count")], [],
                       [{"column": "name", "operator": "=", "value": "user-feedback", "type": "string"}], None, days, env)
accuracy = lfd.metrics("scores-numeric", [("value", "avg"), ("count", "count")], [],
                       [{"column": "name", "operator": "=", "value": "accuracy", "type": "string"}], None, days, env)

n_turns = scalar(turns, "count", "count")
n_tools = scalar(tools, "count", "count")
n_tool_err = scalar(tool_errors, "count", "count")
p50 = scalar(latency, "latency", "p50")
p95 = scalar(latency, "latency", "p95")
cost = scalar(gens, "totalCost", "sum")
tokens = scalar(gens, "totalTokens", "sum")
fb = scalar(feedback, "value", "avg")
acc = scalar(accuracy, "value", "avg")

r1 = st.columns(4)
r1[0].metric("Agent turns", f"{int(n_turns or 0):,}", help="root `agent` observations named loyalty-agent-turn")
r1[1].metric("LLM cost", fmt_usd(cost), delta=f"{fmt_usd((cost or 0) / n_turns)} per turn" if n_turns else None, delta_color="off")
r1[2].metric("Tokens", f"{int(tokens or 0):,}", delta=f"{int(tokens / n_turns):,} per turn" if n_turns and tokens else None, delta_color="off")
r1[3].metric("Latency p50", fmt_ms(p50), delta=f"p95 {fmt_ms(p95)}" if p95 is not None else None, delta_color="off")
r2 = st.columns(4)
r2[0].metric("Tool calls", f"{int(n_tools or 0):,}", help="TOOL observations opened by the PreToolUse hook")
r2[1].metric("Tool error rate", f"{(n_tool_err or 0) / n_tools * 100:.1f}%" if n_tools else "—",
             delta=f"{int(n_tool_err or 0)} failed" if n_tools else None, delta_color="inverse" if n_tool_err else "off")
r2[2].metric("👍 rate", f"{fb * 100:.0f}%" if fb is not None else "—", help="BOOLEAN score `user-feedback`")
r2[3].metric("Accuracy (1-5)", f"{acc:.2f}" if acc is not None else "—", help="NUMERIC score `accuracy` from reviewers")

if st.session_state.get("_lf_last_error"):
    st.caption(f"⚠️ last Langfuse API error: {st.session_state['_lf_last_error']}")

# ---------------------------------------------------------------------------------------
# Charts (each one axis)
# ---------------------------------------------------------------------------------------
gran = "hour" if days <= 1 else "day"
c1, c2 = st.columns(2)

turns_ts = lfd.metrics("observations", [("count", "count")], [], ROOT_FILTER, gran, days, env)
with c1:
    fig = go.Figure()
    col = lfd.value_col(turns_ts, "count", "count")
    if not turns_ts.empty and col:
        fig.add_bar(x=turns_ts["time_dimension"], y=turns_ts[col], marker_color=SERIES[0], name="turns",
                    hovertemplate="%{y} turns<extra></extra>")
    st.plotly_chart(time_axis(base_layout(fig, f"Agent turns per {gran}", "turns")), use_container_width=True)

cost_ts = lfd.metrics("observations", [("totalCost", "sum")], ["providedModelName"], GEN_FILTER, gran, days, env)
with c2:
    fig = go.Figure()
    col = lfd.value_col(cost_ts, "totalCost", "sum")
    if not cost_ts.empty and col:
        models = sorted(cost_ts["providedModelName"].dropna().unique()) if "providedModelName" in cost_ts else []
        for i, m in enumerate(models[:8]):
            sub = cost_ts[cost_ts["providedModelName"] == m]
            fig.add_bar(x=sub["time_dimension"], y=sub[col], name=str(m), marker_color=SERIES[i % 8],
                        hovertemplate="%{y:$.4f}<extra>" + str(m) + "</extra>")
        fig.update_layout(barmode="stack")
    st.plotly_chart(time_axis(base_layout(fig, f"LLM cost per {gran} by model", "USD")), use_container_width=True)

c3, c4 = st.columns(2)
lat_ts = lfd.metrics("observations", [("latency", "p50"), ("latency", "p95")], [], ROOT_FILTER, gran, days, env)
with c3:
    fig = go.Figure()
    c50, c95 = lfd.value_col(lat_ts, "latency", "p50"), lfd.value_col(lat_ts, "latency", "p95")
    if not lat_ts.empty and c50 and c95:
        fig.add_scatter(x=lat_ts["time_dimension"], y=lat_ts[c50] / 1000, mode="lines+markers", name="p50", line=dict(color=SERIES[0], width=2), marker=dict(size=8))
        fig.add_scatter(x=lat_ts["time_dimension"], y=lat_ts[c95] / 1000, mode="lines+markers", name="p95", line=dict(color=SERIES[1], width=2), marker=dict(size=8))
    st.plotly_chart(time_axis(base_layout(fig, "Turn latency (seconds)", "s")), use_container_width=True)

tool_by_name = lfd.metrics("observations", [("count", "count"), ("latency", "p50")], ["name"], TOOL_FILTER, None, days, env)
with c4:
    fig = go.Figure()
    col = lfd.value_col(tool_by_name, "count", "count")
    if not tool_by_name.empty and col:
        top = tool_by_name.sort_values(col, ascending=True).tail(12)
        fig.add_bar(x=top[col], y=top["name"], orientation="h", marker_color=SERIES[2],
                    hovertemplate="%{x} calls<extra>%{y}</extra>")
    fig = base_layout(fig, "Tool calls by tool", "calls", height=340)
    fig.update_yaxes(showgrid=False, title="")
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    st.plotly_chart(fig, use_container_width=True)

c5, c6 = st.columns(2)
by_prompt = lfd.metrics("observations", [("totalCost", "sum"), ("count", "count"), ("latency", "avg")], ["promptVersion"], GEN_FILTER, None, days, env)
with c5:
    fig = go.Figure()
    col = lfd.value_col(by_prompt, "totalCost", "sum")
    if not by_prompt.empty and col and "promptVersion" in by_prompt:
        d = by_prompt.copy()
        d["promptVersion"] = d["promptVersion"].fillna("unlinked").astype(str)
        fig.add_bar(x=d["promptVersion"].map(lambda v: f"v{v}" if v != "unlinked" else v), y=d[col], marker_color=SERIES[0],
                    hovertemplate="%{y:$.4f}<extra>prompt %{x}</extra>")
    st.plotly_chart(base_layout(fig, "LLM cost by prompt version (prompt management link)", "USD"), use_container_width=True)

fb_ts = lfd.metrics("scores-boolean", [("value", "avg"), ("count", "count")], [],
                    [{"column": "name", "operator": "=", "value": "user-feedback", "type": "string"}], gran, days, env)
with c6:
    fig = go.Figure()
    col = lfd.value_col(fb_ts, "value", "avg")
    if not fb_ts.empty and col:
        fig.add_scatter(x=fb_ts["time_dimension"], y=fb_ts[col] * 100, mode="lines+markers", name="👍 rate",
                        line=dict(color=SERIES[0], width=2), marker=dict(size=8), hovertemplate="%{y:.0f}%<extra></extra>")
    fig = time_axis(base_layout(fig, "Thumbs-up rate (BOOLEAN score `user-feedback`)", "%"))
    fig.update_yaxes(range=[0, 100])
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------------------
# Scores detail + tables
# ---------------------------------------------------------------------------------------
scores = lfd.recent_scores(days, environment=env)
s1, s2 = st.columns(2)
with s1:
    fig = go.Figure()
    if not scores.empty:
        acc_df = scores[(scores["name"] == "accuracy")]
        if not acc_df.empty:
            counts = acc_df["value"].astype(float).round().value_counts().sort_index()
            fig.add_bar(x=[str(int(i)) for i in counts.index], y=counts.values, marker_color=SERIES[0], hovertemplate="%{y} ratings<extra>%{x} stars</extra>")
    st.plotly_chart(base_layout(fig, "Reviewer accuracy ratings (NUMERIC score 1-5)", "ratings"), use_container_width=True)
with s2:
    fig = go.Figure()
    if not scores.empty:
        cat = scores[scores["name"] == "issue-category"]
        if not cat.empty:
            counts = cat["value"].astype(str).value_counts().sort_values()
            fig.add_bar(x=counts.values, y=counts.index, orientation="h", marker_color=SERIES[1], hovertemplate="%{x}<extra>%{y}</extra>")
    fig = base_layout(fig, "Issue categories (CATEGORICAL score)", "count")
    fig.update_yaxes(showgrid=False, title="")
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Recent traces")
traces = lfd.recent_traces(days, limit=50, user_id=user_filter, tags=(tag_filter,) if tag_filter else (), environment=env)
if traces.empty:
    st.caption("No traces in this window.")
else:
    st.dataframe(
        traces, use_container_width=True, hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Langfuse", display_text="open ↗"),
            "cost_usd": st.column_config.NumberColumn(format="$%.4f"),
            "latency_s": st.column_config.NumberColumn(format="%.1fs"),
            "timestamp": st.column_config.DatetimeColumn(format="MMM D, HH:mm:ss"),
        },
    )

e1, e2 = st.columns(2)
with e1:
    st.subheader("Recent scores")
    st.dataframe(scores.head(50).astype({"value": str}) if not scores.empty else pd.DataFrame(), use_container_width=True, hide_index=True)
with e2:
    st.subheader("Errors & guardrail hits")
    errs = lfd.observations(days, level="ERROR", limit=50, environment=env)
    warns = lfd.observations(days, obs_type="GUARDRAIL", limit=50, environment=env)
    both = pd.concat([errs, warns], ignore_index=True) if not (errs.empty and warns.empty) else pd.DataFrame()
    st.dataframe(both.astype(str) if not both.empty else both, use_container_width=True, hide_index=True)

with st.expander("How this page queries Langfuse (copy these into a native Langfuse dashboard)"):
    st.markdown(
        "Every panel above is one call to the **Metrics API** (`POST /api/public/metrics`, v2) or a list endpoint. "
        "Langfuse's built-in *Dashboards* feature accepts the same views/measures/dimensions, so you can rebuild this page "
        "natively: Dashboards → New → add widgets with the queries below."
    )
    st.code(json.dumps({
        "turns_per_day": {"view": "observations", "metrics": [{"measure": "count", "aggregation": "count"}], "filters": ROOT_FILTER, "timeDimension": {"granularity": "day"}},
        "cost_by_model": {"view": "observations", "metrics": [{"measure": "totalCost", "aggregation": "sum"}], "dimensions": [{"field": "providedModelName"}], "filters": GEN_FILTER, "timeDimension": {"granularity": "day"}},
        "latency": {"view": "observations", "metrics": [{"measure": "latency", "aggregation": "p95"}], "filters": ROOT_FILTER, "timeDimension": {"granularity": "day"}},
        "tool_calls": {"view": "observations", "metrics": [{"measure": "count", "aggregation": "count"}], "dimensions": [{"field": "name"}], "filters": TOOL_FILTER},
        "thumbs_up_rate": {"view": "scores-boolean", "metrics": [{"measure": "value", "aggregation": "avg"}], "filters": [{"column": "name", "operator": "=", "value": "user-feedback", "type": "string"}], "timeDimension": {"granularity": "day"}},
    }, indent=2), language="json")
