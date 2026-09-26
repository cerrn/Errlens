"""End-to-end test of the 3 demo scenarios against a real LLM.

The demo repository is built from scratch in a temp folder (no GitHub needed). For each
model and scenario:
  1. The pipeline runs (a real LLM call),
  2. The suspect commit and the fixed file are checked,
  3. The generated diff is applied to a copy of the repository with `git apply`,
  4. The failing command is re-run; the scenario PASSES if the error is gone.

The key is read from backend/.env (GEMINI_API_KEY or ANTHROPIC_API_KEY).

Usage (from the backend folder):
    python scripts/live_llm_test.py                         # provider and model from .env
    python scripts/live_llm_test.py --models gemini-2.5-flash,gemini-2.5-flash-lite
    python scripts/live_llm_test.py --models auto           # try every available Gemini model in turn
    python scripts/live_llm_test.py --only bug3 --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.gemini import GeminiLLM  # noqa: E402
from app.main import default_llm_factory  # noqa: E402
from app.models import AnalyzeRequest  # noqa: E402
from app.pipeline import PipelineDeps, run_to_completion  # noqa: E402

TRACES = BACKEND / "tests" / "traces"
# Make child processes print UTF-8 on Windows instead of the legacy code page
UTF8_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
PY = sys.executable
CASES = {
    "bug1": {
        "trace": "demo_bug1_keyerror.txt",
        "cmd": [PY, "-m", "shop", "profile", "1"],
        "commit": "refactor(api): rename user fields",
        "fix_files": {"shop/profile.py"},
        "keywords": ["email_address"],
    },
    "bug2": {
        "trace": "demo_bug2_null_name.txt",
        "cmd": ["node", "scripts/greet.js", "1", "2", "3"],
        "commit": "feat(users): hide inactive users",
        "fix_files": {"scripts/greet.js"},
        "keywords": ["null", "inactive", "active"],
    },
    "bug3": {
        "trace": "demo_bug3_date_format.txt",
        "cmd": [PY, "-m", "shop", "report"],
        "commit": "feat(api): use ISO 8601",
        "fix_files": {"shop/reports.py"},
        "keywords": ["iso", "isoformat", "fromisoformat"],
    },
}

# Free-tier models to try, in order of preference; --models auto picks the available ones.
# Models that scored 3/3 in the live test of 2026-09-26 come first.
GEMINI_PREFERENCE = [
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]


def load_builder():
    spec = importlib.util.spec_from_file_location("build_demo_repo", ROOT / "demo" / "build_demo_repo.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def check_fix(repo: Path, diff: str, cmd: list[str], work_root: Path) -> tuple[bool, str]:
    work = work_root / f"work-{time.time_ns()}"
    shutil.copytree(repo, work)
    (work / "fix.diff").write_text(diff, encoding="utf-8", newline="\n")
    ap = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "fix.diff"],
        cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if ap.returncode != 0:
        return False, f"git apply failed: {ap.stderr.strip()[:200]}"
    run = subprocess.run(
        cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=UTF8_ENV
    )
    if run.returncode != 0:
        last = (run.stderr.strip().splitlines() or ["?"])[-1]
        return False, f"command still fails: {last[:200]}"
    return True, "error fixed"


async def run_case(bug: str, repo: Path, settings: Settings, llm, work_root: Path, verbose: bool) -> dict:
    case = CASES[bug]
    deps = PipelineDeps(settings=settings, storage=None, llm_factory=lambda: llm)
    req = AnalyzeRequest(trace=(TRACES / case["trace"]).read_text(encoding="utf-8"), repo_url=str(repo), ref="main")
    t0 = time.monotonic()
    result, events = await run_to_completion(req, deps)
    elapsed = time.monotonic() - t0
    row: dict = {"bug": bug, "model": llm.model, "seconds": round(elapsed, 1)}
    if result is None:
        row.update(ok=False, note=f"pipeline error: {events[-1].label}")
        return row
    analyze_events = [e for e in events if e.step == "analyze" and e.status != "running"]
    row["analyze_status"] = analyze_events[-1].status if analyze_events else "?"
    row["analyze_label"] = analyze_events[-1].label if analyze_events else ""
    sc = result.suspect_commit
    row["suspect_ok"] = bool(sc and sc.message.startswith(case["commit"]))
    a = result.analysis
    if a is None:
        row.update(ok=False, note=row["analyze_label"] or "no analysis")
        return row
    text = f"{a.summary} {a.root_cause}".lower()
    row["root_cause_mentions"] = any(k in text for k in case["keywords"])
    row["confidence"] = a.confidence
    row["llm_confidence"] = a.llm_confidence
    row["retry"] = "retry" in row["analyze_label"]
    row["summary"] = a.summary
    row["root_cause"] = a.root_cause
    if result.fix is None:
        row.update(fix_file=None, fix_ok=False, ok=False, note=result.meta.fix_unavailable_reason or "no fix")
    else:
        row["fix_file"] = result.fix.file
        row["diff"] = result.fix.diff
        ok, note = check_fix(repo, result.fix.diff, case["cmd"], work_root)
        row.update(fix_ok=ok, note=note)
        row["ok"] = ok and row["suspect_ok"]
    if verbose:
        print(f"\n--- {bug} · {llm.model} ---")
        print("Summary    :", a.summary)
        print("Root cause :", a.root_cause)
        for ev in a.evidence:
            print("  •", ev)
        if result.fix:
            print(result.fix.diff)
    return row


async def resolve_models(settings: Settings, arg: str | None) -> list[str]:
    provider = settings.resolved_llm_provider
    if provider != "gemini":
        return [settings.errlens_model if provider == "anthropic" else ""]
    if not arg:
        return [settings.gemini_model]
    if arg != "auto":
        return [m.strip() for m in arg.split(",") if m.strip()]
    probe = GeminiLLM(settings.gemini_api_key, api_url=settings.gemini_api_url)
    available = set(await probe.list_models())
    chosen = [m for m in GEMINI_PREFERENCE if m in available]
    print("Selected from the available models:", ", ".join(chosen) or "(none)")
    return chosen or [settings.gemini_model]


def make_llm(settings: Settings, model: str):
    if settings.resolved_llm_provider == "gemini":
        return GeminiLLM(
            settings.gemini_api_key, model, max_output_tokens=8192, timeout=settings.llm_timeout,
            api_url=settings.gemini_api_url,
        )
    return default_llm_factory(settings)()


def _utf8_console() -> None:
    """Keeps non-ASCII output (✓/✗) from crashing on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


async def main() -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", help="comma-separated model list, or 'auto' (Gemini only)")
    ap.add_argument("--only", action="append", choices=sorted(CASES))
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--pause", type=float, default=4.0, help="pause between requests in seconds (for free-tier limits)")
    ap.add_argument("--report", default=str(BACKEND / "live_test_report.json"))
    args = ap.parse_args()

    base = Settings()
    if base.resolved_llm_provider is None:
        print("No LLM key found. Add GEMINI_API_KEY or ANTHROPIC_API_KEY to backend/.env.")
        return 2
    settings = base.model_copy(update={"errlens_allow_local_repos": True, "github_token": ""})
    print(f"Provider: {settings.resolved_llm_provider}")

    try:
        models = await resolve_models(settings, args.models)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not list models: {exc}")
        return 1

    bugs = args.only or ["bug3", "bug1", "bug2"]
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="errlens-live-") as tmp:
        tmp_path = Path(tmp)
        repo = tmp_path / "errlens-demo"
        load_builder().build(repo)
        for model in models:
            llm = make_llm(settings, model)
            for i, bug in enumerate(bugs):
                if rows:
                    await asyncio.sleep(args.pause)
                print(f"[{model}] running {bug}...", flush=True)
                try:
                    row = await run_case(bug, repo, settings, llm, tmp_path, args.verbose)
                except Exception as exc:  # noqa: BLE001
                    row = {"bug": bug, "model": model, "ok": False, "note": f"unexpected error: {exc}"}
                rows.append(row)
                mark = "PASS" if row.get("ok") else "FAIL"
                print(
                    f"  {mark:<5} commit={'✓' if row.get('suspect_ok') else '✗'} "
                    f"root-cause={'✓' if row.get('root_cause_mentions') else '✗'} "
                    f"fix={row.get('fix_file') or '-'} confidence={row.get('confidence', '-')} "
                    f"{row.get('seconds', '-')}s · {row.get('note', '')}"
                )

    print("\nSummary")
    for model in models:
        mine = [r for r in rows if r["model"] == model]
        print(f"  {model or settings.resolved_llm_provider}: {sum(1 for r in mine if r.get('ok'))}/{len(mine)} scenarios passed")
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "provider": settings.resolved_llm_provider, "rows": rows}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detailed report: {args.report}")
    return 0 if rows and all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
