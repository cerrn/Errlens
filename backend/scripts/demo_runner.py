"""Runs the demo scenarios against an ErrLens API.

Two uses:
  * Production check: shows with timestamps that the stream really arrives step by step.
  * Cache warm-up: the same requests land in the SQLite cache and return instantly during a demo.

Usage:
    python scripts/demo_runner.py --api https://errlens-api.fly.dev --repo https://github.com/<user>/errlens-demo
    python scripts/demo_runner.py --api http://localhost:8000 --repo ... --only bug3 --ref main
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

TRACES = Path(__file__).resolve().parents[1] / "tests" / "traces"
SCENARIOS = {
    "bug1": "demo_bug1_keyerror.txt",
    "bug2": "demo_bug2_null_name.txt",
    "bug3": "demo_bug3_date_format.txt",
}


def run(api: str, repo: str, ref: str | None, bug: str) -> bool:
    trace = (TRACES / SCENARIOS[bug]).read_text(encoding="utf-8")
    body = {"trace": trace, "repo_url": repo, "ref": ref}
    print(f"\n=== {bug} ===")
    t0 = time.monotonic()
    gaps: list[float] = []
    last = t0
    ok = False
    with httpx.stream("POST", f"{api.rstrip('/')}/analyze/stream", json=body, timeout=200) as r:
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.read().decode(errors='replace')[:300]}")
            return False
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            e = json.loads(line[5:].strip())
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            print(f"{now - t0:6.2f}s  {e['step']:<14} {e['status']:<8} {e['label']}")
            if e["step"] == "done":
                ok = e["status"] == "done"
                if ok:
                    res = e["data"]["result"]
                    fix = res.get("fix")
                    conf = (res.get("analysis") or {}).get("confidence")
                    print(f"         confidence={conf} cached={res['meta']['cached']} fix={'yes' if fix else 'no'} id={res['id']}")
    # If every event arrived at once, a proxy is probably buffering the stream
    if len(gaps) > 4 and max(gaps[1:]) < 0.05 and (last - t0) > 1:
        print("WARNING: events seem to arrive in one batch; SSE buffering may be enabled on a proxy.")
    return ok


def _utf8_console() -> None:
    """Keeps non-ASCII output (✓/✗) from crashing on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--only", choices=sorted(SCENARIOS), action="append")
    args = ap.parse_args()
    results = {b: run(args.api, args.repo, args.ref, b) for b in (args.only or sorted(SCENARIOS))}
    print("\nSummary:", ", ".join(f"{b}={'OK' if v else 'FAIL'}" for b, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
