"""Seed the Langfuse project: prompts (versions + labels), score configs, golden dataset.

Idempotent: re-running adds no duplicate prompt versions or dataset items.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

from agent.config import settings
from agent.evals import seed_dataset
from agent.prompts import JUDGE_PROMPT, PROMPT_VERSIONS
from agent.tracing import get_langfuse, tracing_active


def seed_prompts() -> None:
    lf = get_langfuse()
    try:
        existing = lf.api.prompts.list(name=settings.system_prompt_name).data
        versions = existing[0].versions if existing else []
    except Exception:
        versions = []
    if versions:
        print(f"prompt '{settings.system_prompt_name}' already has versions {versions}; skipping")
    else:
        for v in PROMPT_VERSIONS:
            p = lf.create_prompt(name=settings.system_prompt_name, prompt=v["prompt"], labels=v["labels"], config=v["config"],
                                 type="text", commit_message=v["commit_message"], tags=["loyalty-agent"])
            print(f"created prompt {settings.system_prompt_name} v{p.version} labels={v['labels']}")
    try:
        judge_exists = bool(lf.api.prompts.list(name=settings.judge_prompt_name).data)
    except Exception:
        judge_exists = False
    if not judge_exists:
        lf.create_prompt(name=settings.judge_prompt_name, prompt=JUDGE_PROMPT, labels=["production"], type="text",
                         config={"model": settings.model, "scale": "0-1"}, commit_message="initial judge prompt", tags=["loyalty-agent", "judge"])
        print(f"created prompt {settings.judge_prompt_name}")


def seed_score_configs() -> None:
    lf = get_langfuse()
    try:
        existing = {c.name for c in lf.api.score_configs.get(limit=100).data}
    except Exception:
        existing = set()
    wanted = [
        dict(name="user-feedback", data_type="BOOLEAN", description="Thumbs up/down from the chat UI"),
        dict(name="accuracy", data_type="NUMERIC", min_value=1, max_value=5, description="Reviewer accuracy rating 1-5"),
        dict(name="issue-category", data_type="CATEGORICAL", description="Reviewer-tagged issue",
             categories=[{"label": c, "value": i} for i, c in enumerate(["none", "wrong-number", "wrong-sql", "too-verbose", "missed-definition", "pii"])]),
        dict(name="llm_judge", data_type="NUMERIC", min_value=0, max_value=1, description="Sonnet LLM-as-a-judge helpfulness"),
    ]
    for cfg in wanted:
        if cfg["name"] in existing:
            print(f"score config '{cfg['name']}' exists; skipping")
            continue
        lf.api.score_configs.create(**cfg)
        print(f"created score config {cfg['name']}")


def main() -> None:
    if not tracing_active():
        raise SystemExit("Langfuse is not configured (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL).")
    lf = get_langfuse()
    print("auth check:", lf.auth_check())
    seed_prompts()
    seed_score_configs()
    n = seed_dataset()
    print(f"dataset '{settings.dataset_name}' has {n} items")
    lf.flush()


if __name__ == "__main__":
    main()
