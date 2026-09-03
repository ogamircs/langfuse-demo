"""Replay sample questions across several users/sessions/prompt labels to populate Langfuse.

Adds randomised human feedback scores so the dashboard's score panels have data.
"""
from __future__ import annotations

import argparse
import random
import time

import _bootstrap  # noqa: F401

from agent.context.sample_questions import SAMPLE_QUESTIONS
from agent.runner import run_agent_sync
from agent.tracing import score_trace, tracing_active

USERS = ["analyst.amir", "marketer.jo", "exec.sam", "ds.priya"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    questions = [q["question"] for q in SAMPLE_QUESTIONS if q["category"] != "reasoning"]
    session, sdk_session, turns_left = None, None, 0
    for i in range(args.turns):
        if turns_left == 0:  # start a new multi-turn session
            session, sdk_session, turns_left = f"sim-{rng.randrange(10**6):06d}", None, rng.choice([1, 2, 3])
            user, label = rng.choice(USERS), rng.choice(["production", "production", "experiment"])
        q = rng.choice(questions)
        print(f"[{i + 1}/{args.turns}] {user} · {session} · {label} · {q[:70]}…")
        try:
            r = run_agent_sync(q, user_id=user, session_id=session, sdk_session_id=sdk_session, prompt_label=label,
                               effort=rng.choice(["low", "medium"]), tags=["simulated"], metadata={"surface": "simulate_traffic"})
        except Exception as exc:
            print("   failed:", exc)
            turns_left = 0
            continue
        sdk_session, turns_left = r.sdk_session_id, turns_left - 1
        print(f"   cost ${r.summary.get('cost_usd') or 0:.4f} · {r.summary.get('tool_calls')} tools · {r.trace_url or r.trace_id}")
        if tracing_active() and r.trace_id and rng.random() < 0.7:
            up = rng.random() < 0.8
            score_trace(r.trace_id, name="user-feedback", value=up, comment="simulated")
            score_trace(r.trace_id, name="accuracy", value=float(rng.choice([5, 5, 4, 4, 3]) if up else rng.choice([1, 2, 3])), comment="simulated")
            if not up:
                score_trace(r.trace_id, name="issue-category", value=rng.choice(["wrong-number", "too-verbose", "missed-definition"]), data_type="CATEGORICAL")
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
