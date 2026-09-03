.PHONY: setup seed ui test smoke seed-langfuse experiment traffic langfuse-up langfuse-down

PY ?= .venv/bin/python
STREAMLIT ?= .venv/bin/streamlit

setup:            ## create venv + install deps
	python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "→ edit .env with your ANTHROPIC_API_KEY and Langfuse keys, then: make seed && make ui"

seed:             ## build the synthetic DuckDB warehouse + golden answers
	$(PY) -m data.seed

ui:               ## run the Streamlit chat + dashboard
	$(STREAMLIT) run app/Chat.py

test:             ## offline tests (no LLM, no Langfuse network)
	$(PY) -m pytest -q

smoke:            ## one live agent turn from the CLI (needs ANTHROPIC_API_KEY)
	$(PY) scripts/ask.py "Which customer segment has the highest average basket size?"

seed-langfuse:    ## create prompts (2 versions/labels), score configs and the golden dataset in Langfuse
	$(PY) scripts/seed_langfuse.py

experiment:       ## run the golden dataset through the agent as a Langfuse experiment
	$(PY) scripts/run_experiment.py --label production

traffic:          ## replay sample questions across users/sessions to populate the dashboard
	$(PY) scripts/simulate_traffic.py --turns 12

langfuse-up:      ## self-hosted Langfuse (alternative to Langfuse Cloud)
	docker compose -f docker-compose.langfuse.yml up -d
	@echo "→ open http://localhost:3000 (demo@example.com / demo-password) and set LANGFUSE_BASE_URL=http://localhost:3000 in .env"

langfuse-down:
	docker compose -f docker-compose.langfuse.yml down
