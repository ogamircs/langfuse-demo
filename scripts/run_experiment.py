"""Run the golden dataset through the agent as a Langfuse experiment (dataset run)."""
from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from agent.evals import run_experiment


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="production", help="system prompt label to evaluate")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--judge", action="store_true", help="add the Sonnet LLM-as-a-judge evaluator")
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()
    result = run_experiment(prompt_label=args.label, limit=args.limit, use_llm_judge=args.judge, run_name=args.run_name,
                            max_concurrency=args.concurrency)
    print(result.format())
    if result.dataset_run_url:
        print("dataset run:", result.dataset_run_url)


if __name__ == "__main__":
    main()
