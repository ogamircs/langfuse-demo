"""Read-side access to Langfuse for the dashboard pages (public API via the SDK's `api` client).

Everything returns pandas DataFrames and swallows API errors into empty frames so the
dashboard degrades gracefully. Cached per Streamlit session for `TTL` seconds.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pandas as pd
import streamlit as st

from agent.tracing import get_langfuse, tracing_active

TTL = 60


def _api():
    return get_langfuse().api


def _window(days: float) -> tuple[dt.datetime, dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(days=days), now


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@st.cache_data(ttl=TTL, show_spinner=False)
def metrics(view: str, measures: list[tuple[str, str]], dimensions: list[str], filters: list[dict] | None,
            granularity: str | None, days: float, environment: str | None = None) -> pd.DataFrame:
    """Langfuse Metrics API (v2). measures = [(measure, aggregation)]."""
    if not tracing_active():
        return pd.DataFrame()
    start, end = _window(days)
    flt = list(filters or [])
    if environment:
        flt.append({"column": "environment", "operator": "=", "value": environment, "type": "string"})
    query: dict[str, Any] = {
        "view": view,
        "metrics": [{"measure": m, "aggregation": a} for m, a in measures],
        "dimensions": [{"field": d} for d in dimensions],
        "filters": flt,
        "fromTimestamp": _iso(start),
        "toTimestamp": _iso(end),
    }
    if granularity:
        query["timeDimension"] = {"granularity": granularity}
    try:
        resp = _api().metrics.metrics(query=json.dumps(query))
        rows = [r if isinstance(r, dict) else dict(r) for r in (resp.data or [])]
    except Exception as exc:  # pragma: no cover - network
        st.session_state["_lf_last_error"] = f"metrics({view}): {exc}"
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "time_dimension" in df.columns:
        df["time_dimension"] = pd.to_datetime(df["time_dimension"], utc=True, errors="coerce")
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def recent_traces(days: float, limit: int = 50, user_id: str | None = None, session_id: str | None = None,
                  tags: tuple[str, ...] = (), environment: str | None = None) -> pd.DataFrame:
    if not tracing_active():
        return pd.DataFrame()
    start, end = _window(days)
    try:
        resp = _api().trace.list(limit=limit, from_timestamp=start, to_timestamp=end, user_id=user_id or None,
                                 session_id=session_id or None, tags=list(tags) or None, environment=environment or None,
                                 order_by="timestamp.desc")
    except Exception as exc:  # pragma: no cover
        st.session_state["_lf_last_error"] = f"traces: {exc}"
        return pd.DataFrame()
    rows = []
    for t in resp.data:
        meta = t.metadata if isinstance(t.metadata, dict) else {}
        rows.append({
            "timestamp": t.timestamp, "trace_id": t.id, "name": t.name, "user_id": t.user_id, "session_id": t.session_id,
            "latency_s": getattr(t, "latency", None), "cost_usd": getattr(t, "total_cost", None),
            "tags": ", ".join(t.tags or []), "env": t.environment, "prompt_label": meta.get("prompt_label"),
            "prompt_version": meta.get("prompt_version"), "tool_calls": meta.get("tool_calls"), "is_error": meta.get("is_error"),
            "input": (t.input if isinstance(t.input, str) else json.dumps(t.input, default=str))[:160] if t.input else "",
            "url": f"{get_langfuse()._base_url.rstrip('/')}{t.html_path}" if getattr(t, "html_path", None) else None,
            "scores": len(getattr(t, "scores", None) or []),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=TTL, show_spinner=False)
def recent_scores(days: float, limit: int = 200, environment: str | None = None) -> pd.DataFrame:
    if not tracing_active():
        return pd.DataFrame()
    start, end = _window(days)
    try:
        resp = _api().scores_v3.get_many_v3(limit=limit, from_timestamp=start, to_timestamp=end, environment=environment or None)
    except Exception as exc:  # pragma: no cover
        st.session_state["_lf_last_error"] = f"scores: {exc}"
        return pd.DataFrame()
    rows = []
    for s in resp.data:
        subj = getattr(s, "subject", None)
        rows.append({
            "timestamp": s.timestamp, "name": s.name, "value": s.value, "data_type": getattr(s, "data_type", None),
            "source": s.source, "comment": s.comment, "trace_id": getattr(subj, "trace_id", None) if subj else None,
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=TTL, show_spinner=False)
def observations(days: float, obs_type: str | None = None, limit: int = 200, name: str | None = None,
                 level: str | None = None, environment: str | None = None) -> pd.DataFrame:
    if not tracing_active():
        return pd.DataFrame()
    start, end = _window(days)
    try:
        resp = _api().observations.get_many(limit=limit, type=obs_type, name=name, level=level, from_start_time=start,
                                            to_start_time=end, environment=environment or None)
    except Exception as exc:  # pragma: no cover
        st.session_state["_lf_last_error"] = f"observations: {exc}"
        return pd.DataFrame()
    rows = []
    for o in resp.data:
        rows.append({
            "start_time": o.start_time, "trace_id": o.trace_id, "type": o.type, "name": o.name, "model": getattr(o, "model", None),
            "latency_s": getattr(o, "latency", None), "level": o.level, "status": o.status_message,
            "cost_usd": getattr(o, "calculated_total_cost", None) or (o.cost_details or {}).get("total") if o.cost_details else getattr(o, "calculated_total_cost", None),
            "input_tokens": (o.usage_details or {}).get("input"), "output_tokens": (o.usage_details or {}).get("output"),
            "prompt_version": getattr(o, "prompt_version", None),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=TTL, show_spinner=False)
def sessions(days: float, limit: int = 50, environment: str | None = None) -> pd.DataFrame:
    if not tracing_active():
        return pd.DataFrame()
    start, end = _window(days)
    try:
        resp = _api().sessions.list(limit=limit, from_timestamp=start, to_timestamp=end, environment=environment or None)
    except Exception as exc:  # pragma: no cover
        st.session_state["_lf_last_error"] = f"sessions: {exc}"
        return pd.DataFrame()
    return pd.DataFrame([{"session_id": s.id, "created_at": s.created_at, "env": getattr(s, "environment", None)} for s in resp.data])


def value_col(df: pd.DataFrame, measure: str, aggregation: str) -> str | None:
    """Metrics API returns columns like `count_count` / `totalCost_sum`; find the one we asked for."""
    for cand in (f"{measure}_{aggregation}", measure, aggregation):
        if cand in df.columns:
            return cand
    for c in df.columns:
        if c.startswith(measure):
            return c
    return None


def clear_cache() -> None:
    for fn in (metrics, recent_traces, recent_scores, observations, sessions):
        fn.clear()
