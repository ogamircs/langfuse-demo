"""Shared Streamlit helpers (Langfuse status, formatting, session bootstrap)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from agent.config import settings  # noqa: E402
from agent.tracing import tracing_active  # noqa: E402

DEMO_USERS = ["analyst.amir", "marketer.jo", "exec.sam", "ds.priya"]


def langfuse_banner() -> None:
    """Sidebar status block shown on every page."""
    if tracing_active():
        st.sidebar.success(f"Langfuse: connected\n\n`{settings.langfuse_base_url}`\n\nenv `{settings.langfuse_environment}` · release `{settings.langfuse_release}`")
    else:
        st.sidebar.warning("Langfuse: **not configured** — set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL in `.env`. The agent still runs; nothing is traced.")
    st.sidebar.caption(f"Model `{settings.model}` via Claude Agent SDK")


def fmt_usd(v) -> str:
    return "—" if v is None else f"${v:,.4f}"


def fmt_ms(v) -> str:
    return "—" if v is None else (f"{v / 1000:.1f}s" if v >= 1000 else f"{v:.0f}ms")


def fmt_int(v) -> str:
    return "—" if v is None else f"{int(v):,}"
