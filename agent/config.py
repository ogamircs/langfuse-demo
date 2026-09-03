"""Runtime settings for the demo, loaded from environment variables / .env.

Everything the UI, scripts and tests need to know about the environment lives here so
that the domain layer (agent/context) and the Langfuse glue (agent/tracing) stay free
of os.environ lookups.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Claude Agent SDK
    model: str = field(default_factory=lambda: os.environ.get("AGENT_MODEL", "claude-sonnet-5"))
    effort: str = field(default_factory=lambda: os.environ.get("AGENT_EFFORT", "medium"))
    max_turns: int = field(default_factory=lambda: int(os.environ.get("AGENT_MAX_TURNS", "25")))
    max_budget_usd: float = field(default_factory=lambda: float(os.environ.get("AGENT_MAX_BUDGET_USD", "1.0")))
    permission_mode: str = field(default_factory=lambda: os.environ.get("AGENT_PERMISSION_MODE", "default"))

    # Langfuse
    langfuse_base_url: str = field(
        default_factory=lambda: os.environ.get("LANGFUSE_BASE_URL")
        or os.environ.get("LANGFUSE_HOST")
        or "https://cloud.langfuse.com"
    )
    langfuse_public_key: str | None = field(default_factory=lambda: os.environ.get("LANGFUSE_PUBLIC_KEY"))
    langfuse_secret_key: str | None = field(default_factory=lambda: os.environ.get("LANGFUSE_SECRET_KEY"))
    langfuse_environment: str = field(default_factory=lambda: os.environ.get("LANGFUSE_TRACING_ENVIRONMENT", "development"))
    langfuse_release: str = field(default_factory=lambda: os.environ.get("LANGFUSE_RELEASE", "v0.1.0"))
    tracing_enabled: bool = field(default_factory=lambda: _bool("LANGFUSE_TRACING_ENABLED", True))
    mask_pii: bool = field(default_factory=lambda: _bool("DEMO_MASK_PII", True))

    # Prompt management names
    system_prompt_name: str = "loyalty-agent-system"
    judge_prompt_name: str = "loyalty-agent-judge"
    dataset_name: str = "loyalty-agent-golden"

    # Demo data
    db_path: Path = field(default_factory=lambda: (ROOT / os.environ.get("DEMO_DB_PATH", "data/demo.duckdb")).resolve())
    docs_dir: Path = field(default_factory=lambda: ROOT / "data" / "docs")
    default_user: str = field(default_factory=lambda: os.environ.get("DEMO_DEFAULT_USER", "analyst.amir"))

    @property
    def langfuse_configured(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


settings = Settings()
