"""`utils` — a standalone stdio MCP server started as a subprocess by the Agent SDK.

Demonstrates external MCP wiring (vs. the in-process SDK servers). Run manually with
`python -m agent.tools.external_server` to inspect it with an MCP client.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("utils")

_UNITS = {
    ("kg", "lb"): lambda v: v * 2.20462, ("lb", "kg"): lambda v: v / 2.20462,
    ("km", "mi"): lambda v: v * 0.621371, ("mi", "km"): lambda v: v / 0.621371,
    ("c", "f"): lambda v: v * 9 / 5 + 32, ("f", "c"): lambda v: (v - 32) * 5 / 9,
    ("sqft", "sqm"): lambda v: v * 0.092903, ("sqm", "sqft"): lambda v: v / 0.092903,
    ("l", "gal"): lambda v: v * 0.264172, ("gal", "l"): lambda v: v / 0.264172,
}


@mcp.tool()
def now(timezone_name: str = "America/Halifax") -> str:
    """Current date and time in the given IANA timezone (default America/Halifax)."""
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).isoformat(timespec="seconds")


@mcp.tool()
def days_between(start_date: str, end_date: str, inclusive: bool = True) -> str:
    """Number of days between two ISO dates (YYYY-MM-DD). Inclusive counts both endpoints (campaign days)."""
    s, e = date.fromisoformat(start_date), date.fromisoformat(end_date)
    n = (e - s).days + (1 if inclusive else 0)
    return f"{n} days ({'inclusive' if inclusive else 'exclusive'}) between {s} and {e}"


@mcp.tool()
def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between kg/lb, km/mi, c/f, sqft/sqm, l/gal."""
    key = (from_unit.lower(), to_unit.lower())
    if key not in _UNITS:
        return f"Unsupported conversion {from_unit}->{to_unit}. Supported: {sorted({a for a, _ in _UNITS})}"
    return f"{value} {from_unit} = {_UNITS[key](float(value)):,.4f} {to_unit}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
