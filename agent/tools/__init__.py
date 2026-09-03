"""MCP tool servers exposed to the agent.

Four in-process SDK MCP servers (data, analysis, knowledge, reasoning) and one external
stdio MCP server (utils) so the demo shows both wiring styles. Tool names seen by Claude are
`mcp__<server>__<tool>`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from agent.tools.analysis_tools import SERVER as ANALYSIS_SERVER
from agent.tools.knowledge_tools import SERVER as KNOWLEDGE_SERVER
from agent.tools.reasoning_tools import SERVER as REASONING_SERVER
from agent.tools.sql_tools import SERVER as DATA_SERVER

ROOT = Path(__file__).resolve().parents[2]

EXTERNAL_UTILS_SERVER = {
    "type": "stdio",
    "command": sys.executable,
    "args": ["-m", "agent.tools.external_server"],
    "env": {"PYTHONPATH": str(ROOT)},
}


def build_mcp_servers(include_external: bool = True) -> dict:
    servers = {
        "data": DATA_SERVER,
        "analysis": ANALYSIS_SERVER,
        "knowledge": KNOWLEDGE_SERVER,
        "reasoning": REASONING_SERVER,
    }
    if include_external:
        servers["utils"] = EXTERNAL_UTILS_SERVER
    return servers


ALLOWED_TOOLS = [
    "mcp__data__describe_schema",
    "mcp__data__run_sql",
    "mcp__data__sample_rows",
    "mcp__data__profile_column",
    "mcp__analysis__calculate",
    "mcp__analysis__stats_summary",
    "mcp__analysis__compare_periods",
    "mcp__analysis__naive_forecast",
    "mcp__knowledge__search_docs",
    "mcp__knowledge__get_doc",
    "mcp__reasoning__plan_steps",
    "mcp__reasoning__critique",
    "mcp__reasoning__remember",
    "mcp__reasoning__recall",
    "mcp__utils__now",
    "mcp__utils__days_between",
    "mcp__utils__convert_units",
    "Agent",  # lets the main agent delegate to the sql_analyst subagent
]
