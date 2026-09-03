"""Smoke-run every Streamlit page offline with Streamlit's AppTest (no LLM, no Langfuse)."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from streamlit.testing.v1 import AppTest

PAGES = ["app/Chat.py", "app/pages/1_Dashboard.py", "app/pages/2_Prompts.py", "app/pages/3_Evals.py"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    at = AppTest.from_file(str(ROOT / page), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
