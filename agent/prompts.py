"""Prompt management: the system prompt is served from Langfuse (label-driven) with a local fallback.

Langfuse features shown here:
- versioned prompts with labels (`production`, `experiment`)
- `config` on the prompt (model / effort) that the runner honours
- client-side caching + fallback so the app never breaks when Langfuse is unreachable
- linking every generation to the prompt version that produced it (`propagate_attributes(prompt=...)`)
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.tracing import get_langfuse, tracing_active

CONTEXT = Path(__file__).resolve().parent / "context"
FALLBACK_PROMPT = (CONTEXT / "system_prompt.md").read_text()
SCHEMA_TEXT = (CONTEXT / "schema.md").read_text()

# The versions seeded into Langfuse by scripts/seed_langfuse.py. v1 -> label `production`,
# v2 (adds an explicit "show your SQL" + executive-summary instruction) -> label `experiment`.
PROMPT_VERSIONS: list[dict[str, Any]] = [
    {
        "prompt": FALLBACK_PROMPT,
        "labels": ["production"],
        "config": {"model": settings.model, "effort": "medium", "variant": "baseline"},
        "commit_message": "baseline system prompt",
    },
    {
        "prompt": FALLBACK_PROMPT
        + "\n\nAdditional instructions for this variant:\n"
        "- Start every answer with a one-line executive summary in bold.\n"
        "- After the SQL block, add a 'Caveats' line naming any assumption you made (date ranges, definitions).\n"
        "- When a question involves a campaign, always cite the measurement playbook you used.",
        "labels": ["experiment"],
        "config": {"model": settings.model, "effort": "high", "variant": "exec-summary"},
        "commit_message": "experiment: executive summary + caveats",
    },
]


def template_variables() -> dict[str, str]:
    return {"today": date.today().isoformat(), "schema": SCHEMA_TEXT}


def _compile_local(text: str, variables: dict[str, str]) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: variables.get(m.group(1), m.group(0)), text)


def get_system_prompt(label: str = "production") -> tuple[str, Any, dict[str, Any]]:
    """Return (compiled system prompt, PromptClient or None, metadata for the trace)."""
    variables = template_variables()
    meta: dict[str, Any] = {"prompt_name": settings.system_prompt_name, "prompt_label": label, "prompt_source": "local-fallback",
                            "prompt_version": None, "prompt_config": {}}
    if not tracing_active():
        return _compile_local(FALLBACK_PROMPT, variables), None, meta
    try:
        prompt = get_langfuse().get_prompt(settings.system_prompt_name, label=label, type="text",
                                           fallback=FALLBACK_PROMPT, cache_ttl_seconds=60)
        compiled = prompt.compile(**variables)
        is_fallback = bool(getattr(prompt, "is_fallback", False))
        meta.update({
            "prompt_source": "fallback" if is_fallback else "langfuse",
            "prompt_version": None if is_fallback else getattr(prompt, "version", None),
            "prompt_config": dict(getattr(prompt, "config", {}) or {}),
        })
        return compiled, (None if is_fallback else prompt), meta
    except Exception as exc:  # never block the agent on prompt management
        meta["prompt_error"] = f"{type(exc).__name__}: {exc}"
        return _compile_local(FALLBACK_PROMPT, variables), None, meta


JUDGE_PROMPT = """You are grading an analytics assistant's answer for a grocery loyalty team.
Question:
{{question}}

Expected key facts (may be partial):
{{expected}}

Assistant answer:
{{answer}}

Score the answer from 0.0 to 1.0 on correctness and usefulness. Respond with JSON only:
{"score": <float>, "reasoning": "<one sentence>"}"""
