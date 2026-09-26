"""End to end: the real demo repository (local git) + a fake LLM.

Done criterion: for all 3 demo bugs the root cause is right, the diff applies with
`git apply`, and the error goes away.
"""
import os
import shutil
import subprocess
import sys

import pytest

from app.config import Settings
from app.models import AnalyzeRequest
from app.pipeline import PipelineDeps, run_pipeline, run_to_completion
from app.storage import Storage

from .fakes import DemoLLM, ScriptedLLM

UTF8_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

CASES = {
    "bug1": ("demo_bug1_keyerror.txt", [sys.executable, "-m", "shop", "profile", "1"], "shop/profile.py", "refactor(api)"),
    "bug2": ("demo_bug2_null_name.txt", ["node", "scripts/greet.js", "1", "2", "3"], "scripts/greet.js", "feat(users)"),
    "bug3": ("demo_bug3_date_format.txt", [sys.executable, "-m", "shop", "report"], "shop/reports.py", "feat(api): use ISO"),
}


def settings(tmp_path, **kw) -> Settings:
    return Settings(
        _env_file=None,
        errlens_allow_local_repos=True,
        errlens_db_path=str(tmp_path / "t.db"),
        anthropic_api_key="",
        github_token="",
        **kw,
    )


async def make_deps(tmp_path, llm=None, **kw):
    s = settings(tmp_path, **kw)
    storage = Storage(s.errlens_db_path)
    await storage.init()
    llm = llm or DemoLLM()
    return PipelineDeps(settings=s, storage=storage, llm_factory=lambda: llm), llm


@pytest.mark.parametrize("bug", ["bug1", "bug2", "bug3"])
async def test_demo_end_to_end_fix_applies(bug, demo_repo, traces_dir, tmp_path):
    trace_file, cmd, fix_file, commit_prefix = CASES[bug]
    deps, _ = await make_deps(tmp_path)
    req = AnalyzeRequest(trace=(traces_dir / trace_file).read_text(), repo_url=str(demo_repo), ref="main")
    result, events = await run_to_completion(req, deps)
    assert result is not None, events[-1]

    steps = [(e.step, e.status) for e in events]
    for step in ("parse", "resolve_paths", "fetch_context", "blame", "analyze", "build_fix"):
        assert (step, "running") in steps and (step, "done") in steps, (step, steps)
    assert steps[-1] == ("done", "done")

    assert result.suspect_commit and result.suspect_commit.message.startswith(commit_prefix)
    assert result.fix and result.fix.file == fix_file
    assert result.analysis and 0.7 <= result.analysis.confidence <= 1
    assert len(result.analysis.confidence_reasons) >= 4
    assert result.meta.sha and not result.meta.cached

    # Apply the diff to a copy of the demo repository and re-run the command
    work = tmp_path / "work"
    shutil.copytree(demo_repo, work)
    before = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", env=UTF8_ENV)
    assert before.returncode != 0
    (work / "fix.diff").write_bytes(result.fix.diff.encode("utf-8"))
    subprocess.run(["git", "apply", "--check", "fix.diff"], cwd=work, check=True)
    subprocess.run(["git", "apply", "fix.diff"], cwd=work, check=True)
    after = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", env=UTF8_ENV)
    assert after.returncode == 0, after.stderr


async def test_cache_hit_second_time(demo_repo, traces_dir, tmp_path):
    deps, llm = await make_deps(tmp_path)
    req = AnalyzeRequest(trace=(traces_dir / "demo_bug3_date_format.txt").read_text(), repo_url=str(demo_repo))
    r1, _ = await run_to_completion(req, deps)
    # The same trace with whitespace differences must hit the cache too
    req2 = AnalyzeRequest(trace="\n\n" + req.trace.replace("  File", "    File") + "\n", repo_url=str(demo_repo))
    r2, events = await run_to_completion(req2, deps)
    assert llm.calls == 1
    assert r2.meta.cached and r2.id == r1.id
    assert [e.status for e in events if e.step == "analyze"] == ["skipped"]
    assert r1.meta.warnings and "default branch" in r1.meta.warnings[0]


async def test_parse_error_stops(tmp_path, demo_repo):
    deps, llm = await make_deps(tmp_path)
    events = [e async for e in run_pipeline(AnalyzeRequest(trace="hello world", repo_url=str(demo_repo)), deps)]
    assert [(e.step, e.status) for e in events] == [("parse", "running"), ("parse", "error"), ("done", "error")]
    assert events[-1].data["code"] == "parse_error"
    assert llm.calls == 0


async def test_invalid_repo_url_stops(tmp_path, traces_dir):
    deps, _ = await make_deps(tmp_path)
    req = AnalyzeRequest(trace=(traces_dir / "demo_bug1_keyerror.txt").read_text(), repo_url="https://gitlab.com/x/y")
    events = [e async for e in run_pipeline(req, deps)]
    assert events[-1].step == "done" and events[-1].status == "error"
    assert events[-1].data["code"] == "invalid_repo_url"


async def test_unknown_ref_stops(tmp_path, traces_dir, demo_repo):
    deps, _ = await make_deps(tmp_path)
    req = AnalyzeRequest(trace=(traces_dir / "demo_bug1_keyerror.txt").read_text(), repo_url=str(demo_repo), ref="does-not-exist")
    events = [e async for e in run_pipeline(req, deps)]
    assert events[-1].data["code"] == "ref_not_found"


async def test_unresolved_paths_continue_without_context(tmp_path, traces_dir, demo_repo):
    deps, llm = await make_deps(tmp_path)
    req = AnalyzeRequest(trace=(traces_dir / "py_django_attributeerror.txt").read_text(), repo_url=str(demo_repo))
    result, events = await run_to_completion(req, deps)
    assert result is not None
    statuses = {e.step: e.status for e in events}
    assert statuses["fetch_context"] == "skipped" and statuses["blame"] == "skipped"
    assert result.fix is None and result.analysis is not None
    assert any("not found in the repository" in w for w in result.meta.warnings)
    assert "No code context is available" in llm.last_user_message


async def test_no_llm_configured_returns_partial(tmp_path, traces_dir, demo_repo):
    s = settings(tmp_path)
    storage = Storage(s.errlens_db_path)
    await storage.init()
    deps = PipelineDeps(settings=s, storage=storage, llm_factory=lambda: None)
    req = AnalyzeRequest(trace=(traces_dir / "demo_bug1_keyerror.txt").read_text(), repo_url=str(demo_repo))
    result, events = await run_to_completion(req, deps)
    assert result.analysis is None and result.suspect_commit is not None and result.contexts
    assert "ANTHROPIC_API_KEY" in result.meta.fix_unavailable_reason
    # A result without analysis is not used as a cache hit, but shows up in history
    assert (await storage.get(result.id)) is not None


async def test_invalid_fix_twice_result_without_fix(tmp_path, traces_dir, demo_repo):
    bad = {
        "summary": "s", "root_cause": "r", "evidence": [], "confidence": 0.9,
        "fix": {"file": "shop/api.py", "start_line": 1, "end_line": 1, "new_code": "x", "explanation": "e"},
    }
    deps, llm = await make_deps(tmp_path, llm=ScriptedLLM([bad, bad]))
    req = AnalyzeRequest(trace=(traces_dir / "demo_bug1_keyerror.txt").read_text(), repo_url=str(demo_repo))
    result, events = await run_to_completion(req, deps)
    assert result.fix is None
    assert "failed validation" in result.meta.fix_unavailable_reason
    fix_sig = next(s for s in result.analysis.signals if s.name == "fix_valid")
    assert fix_sig.score == 0
    assert any(e.step == "build_fix" and e.status == "skipped" for e in events)
