import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Tests never talk to a real Langfuse unless a test opts in explicitly.
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")
