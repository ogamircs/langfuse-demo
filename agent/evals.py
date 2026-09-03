"""Datasets, evaluators and experiments (Langfuse `run_experiment`).

- `build_dataset_items()` turns the sample questions + golden answers into dataset items
- `seed_dataset()` creates/updates the dataset in Langfuse (idempotent item ids)
- evaluators: deterministic checks + an optional Sonnet LLM-as-a-judge
- `run_experiment()` runs the agent over the dataset; each item becomes a traced run in Langfuse
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
from pathlib import Path
from typing import Any

from langfuse import Evaluation

from agent.config import settings
from agent.context.sample_questions import SAMPLE_QUESTIONS
from agent.prompts import JUDGE_PROMPT
from agent.tracing import get_langfuse, tracing_active

GOLDEN_PATH = Path(settings.db_path).parent / "golden.json"

# question -> golden key (None = no numeric ground truth; judged qualitatively)
GOLDEN_KEYS: dict[str, str | None] = {
    "simple_sql": "members_total",
    "aggregation": None,  # several aggregation questions; resolved by index below
    "comparison": "private_label_share",
    "campaign_lift": "spring_produce_lift_pct",
    "docs_plus_sql": "active_members_june_2025",
    "forecast": None,
    "external_mcp": "back_to_school",
    "reasoning": "at_risk_members",
    "guardrail": None,
}
_AGGREGATION_KEYS = ["q2_2025_revenue", "top_segment_basket", "top5_points_products", "top_store_rev_per_sqft"]


def load_golden() -> dict[str, dict]:
    return json.loads(GOLDEN_PATH.read_text()) if GOLDEN_PATH.exists() else {}


def build_dataset_items() -> list[dict[str, Any]]:
    golden = load_golden()
    items, agg_i = [], 0
    for q in SAMPLE_QUESTIONS:
        key = GOLDEN_KEYS.get(q["category"])
        if q["category"] == "aggregation":
            key, agg_i = _AGGREGATION_KEYS[agg_i], agg_i + 1
        expected: dict[str, Any] = {"category": q["category"]}
        if key and key in golden:
            expected.update({"golden_key": key, "value": golden[key].get("value"), "facts": golden[key].get("also", {})})
        if q["category"] == "guardrail":
            expected["must_not_contain"] = "@example.com"
        items.append({
            "id": "q-" + hashlib.sha1(q["question"].encode()).hexdigest()[:10],
            "input": {"question": q["question"]},
            "expected_output": expected,
            "metadata": {"category": q["category"], "golden_key": key},
        })
    return items


def seed_dataset(description: str = "Golden questions for the Loyalty Insights Agent") -> int:
    lf = get_langfuse()
    lf.create_dataset(name=settings.dataset_name, description=description,
                      metadata={"source": "data/golden.json", "release": settings.langfuse_release})
    items = build_dataset_items()
    for it in items:
        lf.create_dataset_item(dataset_name=settings.dataset_name, id=it["id"], input=it["input"],
                               expected_output=it["expected_output"], metadata=it["metadata"])
    return len(items)


# --------------------------------------------------------------------------------------
# Evaluators (signature required by Langfuse: keyword args input/output/expected_output/metadata)
# --------------------------------------------------------------------------------------
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _answer(output: Any) -> str:
    if isinstance(output, dict):
        return str(output.get("answer", ""))
    return str(output or "")


def numeric_match(*, input: Any, output: Any, expected_output: Any, metadata: Any = None, **_: Any) -> Evaluation:
    """1.0 if the golden value appears in the answer within 1% (or any listed fact value)."""
    exp = expected_output or {}
    target = exp.get("value") if isinstance(exp, dict) else None
    if target is None:
        return Evaluation(name="numeric_match", value=1.0, comment="no numeric ground truth; skipped", data_type="NUMERIC")
    nums = _numbers(_answer(output))
    tol = max(abs(float(target)) * 0.01, 0.51)
    hit = any(abs(n - float(target)) <= tol for n in nums)
    return Evaluation(name="numeric_match", value=1.0 if hit else 0.0, data_type="NUMERIC",
                      comment=f"expected {target}; found {nums[:8]}")


def mentions_facts(*, output: Any, expected_output: Any, **_: Any) -> Evaluation:
    """Share of expected string facts (banner, segment, store city...) mentioned in the answer."""
    exp = expected_output or {}
    facts = [str(v) for v in (exp.get("facts") or {}).values() if isinstance(v, str)] if isinstance(exp, dict) else []
    if not facts:
        return Evaluation(name="mentions_facts", value=1.0, comment="no string facts expected", data_type="NUMERIC")
    text = _answer(output).lower()
    hits = [f for f in facts if f.lower() in text]
    return Evaluation(name="mentions_facts", value=len(hits) / len(facts), data_type="NUMERIC", comment=f"found {hits} of {facts}")


def shows_sql(*, output: Any, **_: Any) -> Evaluation:
    text = _answer(output)
    ok = "```" in text and bool(re.search(r"\bselect\b", text, re.IGNORECASE))
    return Evaluation(name="shows_sql", value=ok, data_type="BOOLEAN")


def no_pii(*, output: Any, expected_output: Any, **_: Any) -> Evaluation:
    text = _answer(output)
    leak = bool(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\bLC-\d{6,}", text))
    return Evaluation(name="no_pii", value=not leak, data_type="BOOLEAN", comment="PII found in answer" if leak else None)


def answer_length(*, output: Any, **_: Any) -> Evaluation:
    return Evaluation(name="answer_chars", value=float(len(_answer(output))), data_type="NUMERIC")


def llm_judge(*, input: Any, output: Any, expected_output: Any, **_: Any) -> Evaluation:
    """LLM-as-a-judge with Claude Sonnet (Anthropic SDK). Returns 0-1 with reasoning as comment."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return Evaluation(name="llm_judge", value=0.0, comment="anthropic SDK not installed")
    question = input.get("question") if isinstance(input, dict) else str(input)
    prompt = JUDGE_PROMPT
    if tracing_active():
        try:  # judge prompt is itself managed in Langfuse (seeded by scripts/seed_langfuse.py)
            prompt = get_langfuse().get_prompt(settings.judge_prompt_name, type="text", fallback=JUDGE_PROMPT).prompt
        except Exception:
            pass
    filled = (prompt.replace("{{question}}", str(question)).replace("{{expected}}", json.dumps(expected_output, default=str))
              .replace("{{answer}}", _answer(output)[:6000]))
    client = anthropic.Anthropic()
    with client.messages.stream(model=settings.model, max_tokens=512, messages=[{"role": "user", "content": filled}]) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        parsed = json.loads(m.group(0)) if m else {}
        score = float(parsed.get("score", 0.0))
        reasoning = str(parsed.get("reasoning", text))[:500]
    except Exception:
        score, reasoning = 0.0, f"unparseable judge output: {text[:200]}"
    return Evaluation(name="llm_judge", value=max(0.0, min(1.0, score)), data_type="NUMERIC", comment=reasoning,
                      metadata={"judge_model": settings.model, "judge_tokens": msg.usage.output_tokens})


def avg_cost(*, item_results: list, **_: Any) -> list[Evaluation]:
    costs = [r.output.get("cost_usd") for r in item_results if isinstance(r.output, dict) and r.output.get("cost_usd") is not None]
    passes = [e.value for r in item_results for e in r.evaluations if e.name == "numeric_match"]
    out = []
    if costs:
        out.append(Evaluation(name="avg_cost_usd", value=statistics.fmean(costs), data_type="NUMERIC"))
        out.append(Evaluation(name="total_cost_usd", value=sum(costs), data_type="NUMERIC"))
    if passes:
        out.append(Evaluation(name="numeric_pass_rate", value=statistics.fmean(float(p) for p in passes), data_type="NUMERIC"))
    return out


DEFAULT_EVALUATORS = [numeric_match, mentions_facts, shows_sql, no_pii, answer_length]


# --------------------------------------------------------------------------------------
# Experiment runner
# --------------------------------------------------------------------------------------
def make_task(prompt_label: str, run_tag: str):
    from agent.runner import run_agent

    async def task(*, item: Any, **_: Any) -> dict[str, Any]:
        inp = getattr(item, "input", None) if not isinstance(item, dict) else item.get("input")
        question = inp.get("question") if isinstance(inp, dict) else str(inp)
        result = await run_agent(question, user_id="experiment-runner", session_id=run_tag, prompt_label=prompt_label,
                                 tags=["experiment", run_tag], metadata={"surface": "experiment"})
        return {"answer": result.answer, "cost_usd": result.summary.get("cost_usd"), "trace_id": result.trace_id,
                "tool_calls": result.summary.get("tool_calls"), "duration_ms": result.summary.get("duration_ms")}

    return task


def run_experiment(*, prompt_label: str = "production", limit: int | None = None, use_llm_judge: bool = False,
                   run_name: str | None = None, max_concurrency: int = 2):
    """Run the golden dataset through the agent as a Langfuse experiment. Returns ExperimentResult."""
    import datetime as dt

    lf = get_langfuse()
    run_tag = run_name or f"{prompt_label}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    if tracing_active():
        dataset = lf.get_dataset(settings.dataset_name)
        data: Any = list(dataset.items)
    else:  # offline: local items (still runs, nothing is recorded)
        data = build_dataset_items()
    if limit:
        data = data[:limit]
    evaluators = list(DEFAULT_EVALUATORS) + ([llm_judge] if use_llm_judge else [])
    return lf.run_experiment(
        name=f"loyalty-agent · {prompt_label}",
        run_name=run_tag,
        description=f"Golden questions with system prompt label '{prompt_label}' on {settings.model}",
        data=data,
        task=make_task(prompt_label, run_tag),
        evaluators=evaluators,
        run_evaluators=[avg_cost],
        max_concurrency=max_concurrency,
        metadata={"prompt_label": prompt_label, "model": settings.model, "release": settings.langfuse_release},
    )
