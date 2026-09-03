"""`data` MCP server: Text-to-SQL over the read-only DuckDB warehouse."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import duckdb
from claude_agent_sdk import create_sdk_mcp_server, tool

from agent.config import settings
from agent.tracing import current_turn, record_guardrail, traced_tool

MAX_ROWS = 200
QUERY_TIMEOUT_S = 20
BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|install|load|pragma|call|set|reset|truncate|vacuum|checkpoint)\b",
    re.IGNORECASE,
)
PII_COLUMNS = {"email", "loyalty_card"}
SCHEMA_DOC = Path(__file__).resolve().parents[1] / "context" / "schema.md"


def _text(text: str, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["is_error"] = True
    return out


def _connect() -> duckdb.DuckDBPyConnection:
    if not settings.db_path.exists():
        raise FileNotFoundError(f"{settings.db_path} not found — run `python -m data.seed` first")
    return duckdb.connect(str(settings.db_path), read_only=True)


def validate_sql(sql: str) -> str:
    """Return the cleaned statement or raise ValueError. SELECT/WITH/DESCRIBE/SHOW/EXPLAIN only."""
    cleaned = re.sub(r"--[^\n]*", " ", sql)
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL).strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("empty SQL")
    if ";" in cleaned:
        raise ValueError("only one statement per call is allowed")
    if not re.match(r"^(select|with|describe|show|explain)\b", cleaned, re.IGNORECASE):
        raise ValueError("only SELECT / WITH / DESCRIBE / SHOW / EXPLAIN statements are allowed")
    m = BLOCKED.search(cleaned)
    if m:
        raise ValueError(f"statement contains a blocked keyword: {m.group(0).upper()}")
    return cleaned


def _markdown_table(columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "_(no rows)_"
    head = "| " + " | ".join(columns) + " |\n|" + "|".join("---" for _ in columns) + "|"
    body = "\n".join("| " + " | ".join(_fmt(v) for v in r) + " |" for r in rows)
    return head + "\n" + body


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _redact_pii(columns: list[str], rows: list[tuple]) -> tuple[list[tuple], list[str]]:
    hit = [c for c in columns if c.lower() in PII_COLUMNS]
    if not hit:
        return rows, hit
    idx = {i for i, c in enumerate(columns) if c.lower() in PII_COLUMNS}
    return [tuple("[redacted]" if i in idx else v for i, v in enumerate(r)) for r in rows], hit


def run_query(sql: str, limit: int = MAX_ROWS) -> tuple[list[str], list[tuple], int]:
    """Execute a validated read-only query; returns (columns, rows, total_rows_before_limit)."""
    cleaned = validate_sql(sql)
    con = _connect()
    try:
        if re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
            wrapped = f"SELECT * FROM ({cleaned}) AS _q LIMIT {int(limit) + 1}"
        else:
            wrapped = cleaned
        cur = con.execute(wrapped)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
    finally:
        con.close()
    total = len(rows)
    return columns, rows[:limit], total


@tool(
    "describe_schema",
    "Return the warehouse data dictionary (tables, columns, types, join hints). Call this before writing SQL.",
    {},
)
@traced_tool("describe_schema")
async def describe_schema(args: dict[str, Any]) -> dict[str, Any]:
    doc = SCHEMA_DOC.read_text()
    try:
        con = _connect()
        try:
            catalog = con.execute(
                "SELECT table_name, string_agg(column_name || ' ' || data_type, ', ' ORDER BY ordinal_position) "
                "FROM information_schema.columns WHERE table_schema='main' GROUP BY 1 ORDER BY 1"
            ).fetchall()
            bounds = con.execute("SELECT MIN(txn_date), MAX(txn_date), COUNT(*) FROM transactions").fetchone()
        finally:
            con.close()
        live = "\n".join(f"- **{t}**: {cols}" for t, cols in catalog)
        doc += f"\n\n## Live catalog\n{live}\n\nTransactions cover {bounds[0]} to {bounds[1]} ({bounds[2]:,} lines)."
    except Exception as exc:  # schema doc alone is still useful
        doc += f"\n\n_(live catalog unavailable: {exc})_"
    return _text(doc)


@tool(
    "run_sql",
    "Run a read-only DuckDB SQL query (SELECT/WITH only) against the loyalty warehouse and get a markdown table back. "
    "Results are capped at 200 rows; aggregate in SQL rather than pulling raw rows. PII columns are redacted.",
    {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single SELECT or WITH statement (DuckDB dialect)."},
            "limit": {"type": "integer", "description": "Max rows to return (default 50, max 200).", "default": 50},
        },
        "required": ["sql"],
    },
)
@traced_tool("run_sql")
async def run_sql(args: dict[str, Any]) -> dict[str, Any]:
    sql = str(args.get("sql", ""))
    limit = max(1, min(int(args.get("limit") or 50), MAX_ROWS))
    try:
        columns, rows, total = await asyncio.wait_for(asyncio.to_thread(run_query, sql, limit), timeout=QUERY_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _text(f"Query exceeded {QUERY_TIMEOUT_S}s and was cancelled. Add filters or aggregate further.", is_error=True)
    except ValueError as exc:
        record_guardrail(current_turn(), name="sql-write-guard", passed=False, input=sql, details=str(exc))
        return _text(f"Rejected: {exc}", is_error=True)
    except Exception as exc:
        return _text(f"SQL error: {type(exc).__name__}: {exc}", is_error=True)
    rows, pii_hit = _redact_pii(columns, rows)
    if pii_hit:
        record_guardrail(current_turn(), name="pii-output-check", passed=False, input=sql,
                         details=f"redacted PII columns: {', '.join(pii_hit)}")
    truncated = f"\n\n_(showing first {limit} of {total}+ rows)_" if total > limit else f"\n\n_({len(rows)} rows)_"
    note = "\n\n_Note: PII columns were redacted per loyalty privacy policy._" if pii_hit else ""
    return _text(_markdown_table(columns, rows) + truncated + note)


@tool(
    "sample_rows",
    "Preview a few rows of a table to understand its values before writing SQL.",
    {"type": "object", "properties": {"table": {"type": "string"}, "n": {"type": "integer", "default": 5}}, "required": ["table"]},
)
@traced_tool("sample_rows")
async def sample_rows(args: dict[str, Any]) -> dict[str, Any]:
    table = re.sub(r"[^a-zA-Z0-9_]", "", str(args.get("table", "")))
    n = max(1, min(int(args.get("n") or 5), 20))
    try:
        columns, rows, _ = run_query(f"SELECT * FROM {table}", n)
    except Exception as exc:
        return _text(f"Error: {exc}", is_error=True)
    rows, _ = _redact_pii(columns, rows)
    return _text(_markdown_table(columns, rows))


@tool(
    "profile_column",
    "Profile one column: distinct count, nulls, min/max and the top 10 most frequent values.",
    {"type": "object", "properties": {"table": {"type": "string"}, "column": {"type": "string"}}, "required": ["table", "column"]},
)
@traced_tool("profile_column")
async def profile_column(args: dict[str, Any]) -> dict[str, Any]:
    table = re.sub(r"[^a-zA-Z0-9_]", "", str(args.get("table", "")))
    column = re.sub(r"[^a-zA-Z0-9_]", "", str(args.get("column", "")))
    if column.lower() in PII_COLUMNS:
        record_guardrail(current_turn(), name="pii-output-check", passed=False, input=f"{table}.{column}", details="profiling a PII column is not allowed")
        return _text("Profiling PII columns (email, loyalty_card) is not allowed.", is_error=True)
    try:
        cols, stats, _ = run_query(
            f"SELECT COUNT(*) AS rows, COUNT(DISTINCT {column}) AS distinct_values, "
            f"SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS nulls, MIN({column}) AS min_value, MAX({column}) AS max_value FROM {table}", 1)
        tcols, top, _ = run_query(f"SELECT {column} AS value, COUNT(*) AS n FROM {table} GROUP BY 1 ORDER BY 2 DESC", 10)
    except Exception as exc:
        return _text(f"Error: {exc}", is_error=True)
    return _text(f"**{table}.{column}**\n\n{_markdown_table(cols, stats)}\n\nTop values:\n\n{_markdown_table(tcols, top)}")


SERVER = create_sdk_mcp_server(name="data", version="1.0.0", tools=[describe_schema, run_sql, sample_rows, profile_column])
