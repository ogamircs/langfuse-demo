"""`analysis` MCP server: deterministic maths so the model never does arithmetic in its head."""
from __future__ import annotations

import ast
import math
import operator
import statistics
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from agent.tracing import traced_tool

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS: dict[str, Any] = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "sqrt": math.sqrt, "log": math.log,
    "log10": math.log10, "exp": math.exp, "floor": math.floor, "ceil": math.ceil,
    "pct_change": lambda new, old: (new - old) / old * 100 if old else float("nan"),
    "pct_of": lambda part, whole: part / whole * 100 if whole else float("nan"),
    "cagr": lambda start, end, years: ((end / start) ** (1 / years) - 1) * 100 if start and years else float("nan"),
}


def safe_eval(expression: str) -> float:
    """Evaluate an arithmetic expression without exposing Python eval."""
    tree = ast.parse(expression.strip(), mode="eval")

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return _UN[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(e) for e in node.elts]
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    result = ev(tree)
    if isinstance(result, list):
        raise ValueError("expression must evaluate to a single number")
    return float(result)


def _text(text: str, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["is_error"] = True
    return out


@tool(
    "calculate",
    "Evaluate an arithmetic expression exactly. Supports + - * / ** %, parentheses and the functions "
    "abs, round, min, max, sum, sqrt, log, log10, exp, floor, ceil, pct_change(new, old), pct_of(part, whole), cagr(start, end, years).",
    {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
)
@traced_tool("calculate")
async def calculate(args: dict[str, Any]) -> dict[str, Any]:
    expr = str(args.get("expression", ""))
    try:
        value = safe_eval(expr)
    except Exception as exc:
        return _text(f"Could not evaluate `{expr}`: {exc}", is_error=True)
    return _text(f"{expr} = {value:,.4f}".rstrip("0").rstrip("."))


@tool(
    "stats_summary",
    "Descriptive statistics (count, mean, median, stdev, min, max, p25, p75) for a list of numbers.",
    {"type": "object", "properties": {"values": {"type": "array", "items": {"type": "number"}}}, "required": ["values"]},
)
@traced_tool("stats_summary")
async def stats_summary(args: dict[str, Any]) -> dict[str, Any]:
    values = [float(v) for v in args.get("values", [])]
    if not values:
        return _text("values is empty", is_error=True)
    s = sorted(values)
    q = statistics.quantiles(s, n=4) if len(s) >= 2 else [s[0], s[0], s[0]]
    out = {
        "count": len(s), "mean": statistics.fmean(s), "median": statistics.median(s),
        "stdev": statistics.stdev(s) if len(s) > 1 else 0.0, "min": s[0], "max": s[-1], "p25": q[0], "p75": q[2],
    }
    return _text("\n".join(f"- {k}: {v:,.4f}" if isinstance(v, float) else f"- {k}: {v}" for k, v in out.items()))


@tool(
    "compare_periods",
    "Compare a metric between two periods: absolute change, percent change and (optionally) per-day normalisation.",
    {
        "type": "object",
        "properties": {
            "current": {"type": "number"}, "previous": {"type": "number"},
            "current_days": {"type": "integer"}, "previous_days": {"type": "integer"},
            "label": {"type": "string"},
        },
        "required": ["current", "previous"],
    },
)
@traced_tool("compare_periods")
async def compare_periods(args: dict[str, Any]) -> dict[str, Any]:
    cur, prev = float(args["current"]), float(args["previous"])
    cd, pd = args.get("current_days"), args.get("previous_days")
    lines = [f"**{args.get('label', 'metric')}**", f"- current: {cur:,.2f}", f"- previous: {prev:,.2f}",
             f"- absolute change: {cur - prev:,.2f}",
             f"- percent change: {((cur - prev) / prev * 100) if prev else float('nan'):,.2f}%"]
    if cd and pd:
        c_day, p_day = cur / int(cd), prev / int(pd)
        lines += [f"- current per day: {c_day:,.2f} ({cd} days)", f"- previous per day: {p_day:,.2f} ({pd} days)",
                  f"- per-day lift: {((c_day - p_day) / p_day * 100) if p_day else float('nan'):,.2f}%"]
    return _text("\n".join(lines))


@tool(
    "naive_forecast",
    "Simple forecast of the next `horizon` points from a numeric series using mean, last-value or linear trend.",
    {
        "type": "object",
        "properties": {
            "series": {"type": "array", "items": {"type": "number"}},
            "horizon": {"type": "integer", "default": 1},
            "method": {"type": "string", "enum": ["mean", "last", "linear"], "default": "linear"},
        },
        "required": ["series"],
    },
)
@traced_tool("naive_forecast")
async def naive_forecast(args: dict[str, Any]) -> dict[str, Any]:
    series = [float(v) for v in args.get("series", [])]
    horizon = max(1, int(args.get("horizon") or 1))
    method = args.get("method") or "linear"
    if len(series) < 2:
        return _text("need at least 2 points", is_error=True)
    n = len(series)
    if method == "mean":
        preds = [statistics.fmean(series)] * horizon
    elif method == "last":
        preds = [series[-1]] * horizon
    else:
        xs = list(range(n))
        xbar, ybar = statistics.fmean(xs), statistics.fmean(series)
        slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, series)) / sum((x - xbar) ** 2 for x in xs)
        intercept = ybar - slope * xbar
        preds = [intercept + slope * (n + h) for h in range(horizon)]
    return _text(f"method={method}, history={n} points\n" + "\n".join(f"- t+{h + 1}: {p:,.2f}" for h, p in enumerate(preds)))


SERVER = create_sdk_mcp_server(name="analysis", version="1.0.0", tools=[calculate, stats_summary, compare_periods, naive_forecast])
