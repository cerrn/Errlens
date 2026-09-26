import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# Tests must never use real API keys
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GITHUB_TOKEN"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "auto"

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_demo_repo", ROOT / "demo" / "build_demo_repo.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def demo_repo(tmp_path_factory) -> Path:
    """Builds the demo repository in a temp folder with a fixed 'now'."""
    target = tmp_path_factory.mktemp("demo") / "errlens-demo"
    _load_builder().build(target, now=NOW)
    return target


@pytest.fixture
def traces_dir() -> Path:
    return Path(__file__).parent / "traces"
