"""Tool implementations without an LLM in the loop."""
import asyncio
from pathlib import Path

import pytest

from agent.config import settings
from agent.tools import ALLOWED_TOOLS, build_mcp_servers
from agent.tools.analysis_tools import calculate, naive_forecast, safe_eval, stats_summary
from agent.tools.knowledge_tools import get_doc, get_index, search_docs
from agent.tools.reasoning_tools import critique, plan_steps, recall, remember
from agent.tools.sql_tools import describe_schema, profile_column, run_query, run_sql, sample_rows, validate_sql


def call(tool, args=None):
    return asyncio.run(tool.handler(args or {}))


def text(result):
    return "\n".join(c["text"] for c in result["content"])


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    if not settings.db_path.exists():
        from data.seed import build
        build()
    yield


# ---- SQL guardrails ------------------------------------------------------------------
@pytest.mark.parametrize("sql", ["DELETE FROM customers", "SELECT 1; DROP TABLE customers", "CREATE TABLE x AS SELECT 1",
                                 "INSERT INTO customers VALUES (1)", "PRAGMA version", "SET threads=1", "COPY customers TO 'x.csv'"])
def test_validate_sql_rejects_writes(sql):
    with pytest.raises(ValueError):
        validate_sql(sql)


def test_validate_sql_accepts_reads():
    assert validate_sql("  SELECT 1 -- comment\n;") == "SELECT 1"
    assert validate_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_run_query_limits_rows():
    cols, rows, total = run_query("SELECT * FROM transactions", limit=5)
    assert len(rows) == 5 and total == 6 and "txn_id" in cols


def test_run_sql_tool_redacts_pii():
    out = text(call(run_sql, {"sql": "SELECT customer_id, email, loyalty_card FROM customers LIMIT 3"}))
    assert "[redacted]" in out and "@example.com" not in out and "PII" in out


def test_run_sql_tool_rejects_write():
    res = call(run_sql, {"sql": "DROP TABLE customers"})
    assert res.get("is_error") and "Rejected" in text(res)


def test_describe_schema_includes_live_catalog():
    out = text(call(describe_schema))
    assert "## customers" in out and "Live catalog" in out and "transactions" in out


def test_sample_and_profile():
    assert "| customer_id" in text(call(sample_rows, {"table": "customers", "n": 2}))
    out = text(call(profile_column, {"table": "customers", "column": "segment"}))
    assert "distinct_values" in out and "Family Stock-Up" in out
    assert call(profile_column, {"table": "customers", "column": "email"}).get("is_error")


def test_golden_answers_match_db():
    import json
    golden = json.loads((Path(settings.db_path).parent / "golden.json").read_text())
    _, rows, _ = run_query("SELECT COUNT(*) FROM customers")
    assert rows[0][0] == golden["members_total"]["value"]


# ---- analysis ---------------------------------------------------------------------------
def test_safe_eval():
    assert safe_eval("pct_change(150, 100)") == 50.0
    assert safe_eval("round(sqrt(16) + 2 ** 3, 2)") == 12.0
    for bad in ["__import__('os')", "open('x')", "1 if 1 else 2", "[1,2]"]:
        with pytest.raises(Exception):
            safe_eval(bad)


def test_calculate_tool():
    assert "= 3" in text(call(calculate, {"expression": "1 + 2"}))
    assert call(calculate, {"expression": "import os"}).get("is_error")
    assert "mean" in text(call(stats_summary, {"values": [1, 2, 3, 4]}))
    assert "t+1" in text(call(naive_forecast, {"series": [10, 20, 30], "horizon": 2}))


# ---- knowledge ---------------------------------------------------------------------------
def test_retriever_ranks_relevant_doc():
    hits = get_index().search("what is an active member", k=2)
    assert hits and hits[0][0].doc_id == "loyalty-program-rules"
    assert "lapse" in text(call(search_docs, {"query": "members at risk of lapsing"})).lower()
    assert call(get_doc, {"doc_id": "nope"}).get("is_error")
    assert "Campaign Measurement" in text(call(get_doc, {"doc_id": "campaign-measurement-playbook"}))


# ---- reasoning ---------------------------------------------------------------------------
def test_reasoning_tools():
    plan = text(call(plan_steps, {"goal": "measure campaign lift for Spring Fresh Produce"}))
    assert "baseline" in plan.lower() and "1." in plan
    verdict = text(call(critique, {"question": "How many members?", "draft": "Lots of members, see member1@example.com"}))
    assert "PII" in verdict and "no numbers" in verdict
    assert "PASS" in text(call(critique, {"question": "How many members?", "draft": "1,500 members.\n```sql\nSELECT COUNT(*) FROM customers\n```"}))
    call(remember, {"key": "q2", "value": "1.14M"})
    assert "1.14M" in text(call(recall, {"key": "q2"}))


# ---- registry ------------------------------------------------------------------------------
def test_registry_names_match_allowed_tools():
    servers = build_mcp_servers(include_external=False)
    names = set()
    for server, cfg in servers.items():
        assert cfg["type"] == "sdk"
        names |= {f"mcp__{server}__{t.name}" for t in cfg["instance"]._tool_manager.list_tools()} if hasattr(cfg["instance"], "_tool_manager") else set()
    for allowed in ALLOWED_TOOLS:
        if allowed.startswith("mcp__") and not allowed.startswith("mcp__utils__") and names:
            assert allowed in names, allowed


async def _list_external_tools():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from agent.tools import EXTERNAL_UTILS_SERVER
    params = StdioServerParameters(command=EXTERNAL_UTILS_SERVER["command"], args=EXTERNAL_UTILS_SERVER["args"], env={**__import__("os").environ, **EXTERNAL_UTILS_SERVER["env"]})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            res = await s.call_tool("days_between", {"start_date": "2025-08-18", "end_date": "2025-09-07"})
            return [t.name for t in tools.tools], res.content[0].text


def test_external_stdio_server():
    names, out = asyncio.run(_list_external_tools())
    assert {"now", "days_between", "convert_units"} <= set(names)
    assert out.startswith("21 days")
