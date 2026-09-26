"""Builds the ErrLens demo repository from scratch.

Creates a clean initial commit, then one realistic commit per bug. Commit dates are
relative to the day you run the script: the initial commit lands 30 days ago and the
bug commits within the last few days, so ErrLens' "changed in the last 14 days" signal
still works on demo day.

Usage:
    python demo/build_demo_repo.py ../errlens-demo
    cd ../errlens-demo && git remote add origin https://github.com/<user>/errlens-demo.git
    git push -u origin main
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEMPLATE = Path(__file__).parent / "template"

AUTHORS = {
    "alex": ("Alex Morgan", "alex@example.com"),
    "sam": ("Sam Lee", "sam@example.com"),
    "jordan": ("Jordan Kim", "jordan@example.com"),
}

# Each commit: (author, days ago, hour, message, [(file, old, new), ...])
BUG_COMMITS = [
    (
        "sam",
        3,
        11,
        "refactor(api): rename user fields to match v2 schema\n\n"
        "The v2 user schema uses `user_id` and `email_address` instead of `id` and `email`.\n"
        "Updated serialize_user and the profile page accordingly.",
        [
            ("shop/api.py", '        "id": user.id,\n        "name": user.name,\n        "email": user.email,',
             '        "user_id": user.id,\n        "name": user.name,\n        "email_address": user.email,'),
            ("shop/profile.py", '        "id": user_data["id"],', '        "id": user_data["user_id"],'),
        ],
    ),
    (
        "jordan",
        2,
        15,
        "feat(users): hide inactive users from lookups\n\n"
        "Inactive accounts should not be visible to other scripts, so findUser\n"
        "now returns null for them.",
        [
            ("scripts/users.js", "  const user = USERS.find((u) => u.id === id);\n  return user;",
             "  const user = USERS.find((u) => u.id === id);\n  if (!user || !user.active) return null;\n  return user;"),
        ],
    ),
    (
        "alex",
        1,
        17,
        "feat(api): use ISO 8601 timestamps in API responses\n\n"
        "Mobile clients need the time component as well, switch order dates to\n"
        "ISO 8601 (e.g. 2026-09-20T14:31:05).",
        [
            ("shop/dates.py", '    return dt.strftime("%Y-%m-%d")', '    return dt.isoformat(timespec="seconds")'),
        ],
    ),
]


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
    ).stdout.strip()


def commit(repo: Path, author: str, when: datetime, message: str) -> str:
    name, email = AUTHORS[author]
    stamp = when.isoformat()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-q", "-m", message, env=env)
    return git(repo, "rev-parse", "--short", "HEAD")


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{path}: expected text not found:\n{old}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def build(target: Path, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc).astimezone()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} is not empty. Pick another folder or delete it first.")
    target.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE.rglob("*"):
        if src.is_file() and "__pycache__" not in src.parts:
            dst = target / src.relative_to(TEMPLATE)
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Pin line endings to LF so blame line numbers stay consistent on Windows too
            dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))
    (target / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")

    git(target, "init", "-q")
    git(target, "checkout", "-q", "-b", "main")
    log = []
    start = (now - timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)
    log.append(commit(target, "alex", start, "Initial commit: user/order service and greeting script"))
    for author, days_ago, hour, message, edits in BUG_COMMITS:
        for rel, old, new in edits:
            replace_in(target / rel, old, new)
        when = (now - timedelta(days=days_ago)).replace(hour=hour, minute=12, second=0, microsecond=0)
        log.append(commit(target, author, when, message))
    return log


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    target = Path(argv[0]).resolve()
    shas = build(target)
    print(f"Demo repository created: {target}")
    print(git(target, "log", "--oneline", "--date=short", "--format=%h %ad %an  %s"))
    print("\nNext step: create an empty 'errlens-demo' repository on GitHub and push to it.")
    return 0 if shas else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
