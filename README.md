# Langfuse demo · Loyalty Insights Agent

A tool-rich AI agent built with the **Claude Agent SDK** (Claude Sonnet) whose every step is observable in
**Langfuse**. The point of the repo is to show Langfuse features end to end on a realistic agent, not the
domain: the grocery-loyalty context is a thin, swappable layer.

```
Streamlit UI ──▶ agent/runner.py ──▶ Claude Agent SDK (claude-sonnet-5)
   │                 │                    │  hooks: PreToolUse / PostToolUse / Subagent* / Stop
   │                 │                    ├─ in-process MCP servers: data (Text-to-SQL), analysis, knowledge, reasoning
   │                 │                    └─ external stdio MCP server: utils
   │                 └─ agent/tracing.py ──▶ Langfuse (traces · sessions · users · prompts · scores · datasets · experiments)
   └─ Dashboard page ◀── Langfuse public API (Metrics API, traces, observations, scores)
```

## What you can demo, feature by feature

| Langfuse feature | Where it is in the code | What to show |
|---|---|---|
| **Tracing** with typed observations (`agent`, `generation`, `tool`, `retriever`, `chain`, `evaluator`, `guardrail`) | `agent/tracing.py` (`turn_trace`, `build_hooks`, `traced_tool`) | One trace per chat turn: root `agent` → `generation`s with tokens/cost/TTFT → `tool` spans opened by SDK hooks → `.impl` spans from inside the tool → subagent `agent` spans |
| **Sessions & users** | `propagate_attributes(user_id, session_id, tags, metadata)` in `turn_trace` | One chat thread = one Langfuse session; pick the user in the sidebar and filter in Langfuse |
| **Token usage & cost** | `usage_details_from_anthropic`, `estimate_cost`, `ResultMessage.model_usage` | Per-generation usage incl. cache read/write, explicit `cost_details`, SDK totals in root metadata |
| **Prompt management** (versions, labels, config, fallback, prompt↔generation linking) | `agent/prompts.py`, `scripts/seed_langfuse.py`, Prompts page | Switch `production`/`experiment` label in the sidebar; promote a version; cost-by-prompt-version chart |
| **Scores** (BOOLEAN / NUMERIC / CATEGORICAL, human + automated) | `score_trace`, `record_evaluator`, chat feedback widgets | 👍/👎, 1–5 accuracy, issue category; in-process heuristic evaluator scores every turn |
| **Guardrails** | `record_guardrail` in `agent/tools/sql_tools.py` | Ask for e-mails → PII redacted, `guardrail` observation flagged WARNING |
| **Datasets & experiments** (`run_experiment`, evaluators, run-level evaluators) | `agent/evals.py`, `scripts/run_experiment.py`, Evals page | Golden dataset from `data/golden.json`; compare `production` vs `experiment` runs in Langfuse |
| **LLM-as-a-judge** | `llm_judge` in `agent/evals.py` (+ managed judge prompt `loyalty-agent-judge`) | Sonnet grades answers 0–1; also configure Langfuse's UI-managed evaluator on live traces |
| **Masking / PII** | `mask_pii` passed to `Langfuse(mask=...)` | E-mails and loyalty card numbers never reach Langfuse |
| **Environments & releases** | `LANGFUSE_TRACING_ENVIRONMENT`, `LANGFUSE_RELEASE` | Filter the dashboard by environment |
| **Metrics API / public API** | `app/langfuse_data.py`, Dashboard page | Turns, cost, latency p50/p95, tool calls, error rate, feedback — plus the JSON to rebuild it as a native Langfuse dashboard |
| **Multi-turn & subagents** | `resume=` in `agent/runner.py`, `SUBAGENTS` | Follow-up questions resume the SDK session; `sql_analyst` subagent appears as a nested `agent` span |

## Quick start

Requirements: Python 3.10+, an Anthropic API key, a Langfuse project (Cloud or self-hosted).

```bash
make setup                 # venv + deps, copies .env.example → .env
# edit .env: ANTHROPIC_API_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL
make seed                  # builds data/demo.duckdb (≈600k transaction lines) + data/golden.json
make seed-langfuse         # prompts (2 versions/labels), score configs, golden dataset
make ui                    # http://localhost:8501
```

Then, in the UI:
1. **Chat** — ask a sample question; open the *Trace timeline* under the answer and the trace link in Langfuse.
2. Leave 👍/👎 and a rating → scores appear on the trace.
3. **Prompts** page — promote v2 to `production`, ask again, watch the answer style change.
4. **Evals** page — run the golden dataset with both labels; compare runs in Langfuse → Datasets.
5. **Dashboard** page — cost, latency, tool usage, feedback; or `make traffic` first to populate it.

### Langfuse Cloud (default)
Create a project at https://cloud.langfuse.com (EU) or https://us.cloud.langfuse.com (US), copy the keys into
`.env`, set `LANGFUSE_BASE_URL` to the matching host.

### Self-hosted alternative
```bash
make langfuse-up           # docker compose -f docker-compose.langfuse.yml up -d
```
Login `demo@example.com` / `demo-password`; the compose file pre-creates the project with the keys
`pk-lf-demo-public-key` / `sk-lf-demo-secret-key` (see `.env.example`). Change all secrets before exposing it.

## CLI equivalents

```bash
.venv/bin/python scripts/ask.py "Which segment has the highest average basket size?"   # one turn, streamed
.venv/bin/python scripts/ask.py --resume <sdk-session-id> "and for Quebec only?"           # multi-turn
.venv/bin/python scripts/run_experiment.py --label experiment --judge                     # experiment + LLM judge
.venv/bin/python scripts/simulate_traffic.py --turns 20                                   # populate the dashboard
make test                                                                                 # offline tests
```

## How the tracing works

```
trace  user_id=analyst.amir  session_id=chat-…  tags=[claude-agent-sdk, prompt:production, effort:medium]
└── agent  loyalty-agent-turn            input=question  output=answer  metadata={cost, turns, model_usage…}
    ├── generation  claude-sonnet-5      usage_details={input, output, cache_read…}  cost_details  completion_start_time
    ├── tool  mcp__knowledge__search_docs   (PreToolUse → PostToolUse hooks)
    │   └── retriever  search_docs.impl     (from inside the tool: query, hits)
    ├── generation  claude-sonnet-5
    ├── tool  mcp__data__run_sql
    │   └── tool  run_sql.impl
    ├── guardrail  pii-output-check      (only when PII columns were requested)
    ├── agent  subagent:sql_analyst      (SubagentStart/Stop hooks; its tool calls nest here)
    ├── generation  claude-sonnet-5
    └── evaluator  heuristic-quality     + scores cites_numbers / shows_sql / pii_leak / answer_chars
```

- The Claude Agent SDK streams `StreamEvent`s (used for TTFT and live tokens), `AssistantMessage`s (one
  generation each, with model + usage) and a final `ResultMessage` (cost, per-model usage, turns).
- SDK **hooks** run in-process, so each `PreToolUse` opens a Langfuse `tool` observation keyed by
  `tool_use_id` and `PostToolUse`/`PostToolUseFailure` closes it. Sub-agent hooks carry `agent_id`, which is
  used to parent their tool spans correctly.
- Tool implementations are wrapped with `traced_tool`, which attaches an `.impl` observation under the hook
  span via `trace_context`, so you see both the protocol-level call and what happened inside.
- Nothing here requires Langfuse to be reachable: with no keys the SDK is created with `tracing_enabled=False`
  and every helper is a no-op; prompt management falls back to `agent/context/system_prompt.md`.

## Swap in your own context

Only three places know about groceries:

- `agent/context/system_prompt.md`, `schema.md`, `sample_questions.py`
- `data/seed.py` (or point `DEMO_DB_PATH` at any DuckDB file; the SQL tool is read-only) and `data/docs/*.md`
- `agent/tools/reasoning_tools.py::_PLAYBOOK_HINTS` (optional business-rule hints for `plan_steps`)

The golden answers in `data/golden.json` are computed by `data/seed.py`; regenerate them for a new dataset
or delete the `value` fields and rely on `mentions_facts` + the LLM judge.

## Tools exposed to the agent (MCP)

| Server | Kind | Tools |
|---|---|---|
| `data` | in-process SDK MCP | `describe_schema`, `run_sql` (SELECT-only DuckDB, 200-row cap, PII redaction), `sample_rows`, `profile_column` |
| `analysis` | in-process | `calculate` (AST-safe), `stats_summary`, `compare_periods`, `naive_forecast` |
| `knowledge` | in-process | `search_docs` (BM25 over `data/docs`), `get_doc` |
| `reasoning` | in-process | `plan_steps`, `critique`, `remember`, `recall` |
| `utils` | external stdio MCP (`python -m agent.tools.external_server`) | `now`, `days_between`, `convert_units` |
| built-in | Claude Agent SDK | `Agent` (delegates to the `sql_analyst` subagent) |

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | required by the Claude Agent SDK |
| `AGENT_MODEL` / `AGENT_EFFORT` / `AGENT_MAX_TURNS` / `AGENT_MAX_BUDGET_USD` | agent defaults (`claude-sonnet-5`, `medium`, 25, $1) |
| `AGENT_PERMISSION_MODE` | `default` (auto-approves the allowlisted MCP tools; works as any user) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | Langfuse project |
| `LANGFUSE_TRACING_ENVIRONMENT` / `LANGFUSE_RELEASE` | environment + release tagging |
| `LANGFUSE_SAMPLE_RATE`, `LANGFUSE_TRACING_ENABLED`, `LANGFUSE_DEBUG` | standard Langfuse SDK knobs |
| `DEMO_MASK_PII` | apply the masking function before export (default true) |

## Tests

`make test` runs offline: tool guardrails, retriever ranking, evaluators, and a full **span-tree test** that
points the Langfuse SDK at a local OTLP capture stub (`tests/otlp_stub.py`) and asserts the exact
observation tree, attributes (user/session/tags/usage/cost/masking) and parenting the demo produces.
