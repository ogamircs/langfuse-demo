"""Ask the agent one question from the CLI (streams the answer, prints the Langfuse trace)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import _bootstrap  # noqa: F401

from agent.runner import run_agent


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="+")
    ap.add_argument("--user", default=None)
    ap.add_argument("--label", default="production")
    ap.add_argument("--effort", default=None)
    ap.add_argument("--resume", default=None, help="Claude SDK session id to continue")
    args = ap.parse_args()

    def on_event(e: dict) -> None:
        if e["kind"] == "tool_start":
            print(f"\n  🔧 {e['tool']} {json.dumps(e.get('input'))[:150]}", file=sys.stderr)

    r = await run_agent(" ".join(args.question), user_id=args.user, prompt_label=args.label, effort=args.effort,
                        sdk_session_id=args.resume, on_text=lambda t: print(t, end="", flush=True), on_event=on_event, tags=["cli"])
    print("\n\n--- summary ---")
    print(json.dumps({k: v for k, v in r.summary.items() if k != "usage"}, indent=2, default=str))
    print("trace:", r.trace_url or r.trace_id, "| resume with --resume", r.sdk_session_id)


if __name__ == "__main__":
    asyncio.run(main())
