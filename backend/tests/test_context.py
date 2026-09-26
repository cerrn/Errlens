import pytest

from app.context import (
    ErrorSignature,
    fetch_contexts,
    find_suspect,
    resolve_frames,
    resolve_path,
)
from app.github_client import LocalGitSource, parse_repo_url
from app.parser import parse_trace

from .conftest import NOW

TREE = [
    "app/__init__.py",
    "app/user.py",
    "app/models/user.py",
    "tests/test_user.py",
    "scripts/greet.js",
    "lib/utils/__init__.py",
    "src/utils/__init__.py",
]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/home/deploy/project/app/user.py", "app/user.py"),
        ("/srv/x/app/models/user.py", "app/models/user.py"),
        ("C:\\Users\\dev\\proj\\app\\user.py", "app/user.py"),
        ("app/user.py", "app/user.py"),
        ("/home/claude/errlens-demo/scripts/greet.js", "scripts/greet.js"),
        ("/tmp/other/missing.py", None),
        ("/x/utils/__init__.py", None),  # ambiguous: two candidates tie
        ("/x/lib/utils/__init__.py", "lib/utils/__init__.py"),
    ],
)
def test_resolve_path(raw, expected):
    assert resolve_path(raw, TREE) == expected


async def _prepare(demo_repo, trace_text):
    source = LocalGitSource(parse_repo_url(str(demo_repo), allow_local=True))
    ref, sha = await source.resolve_ref(None)
    tree, _ = await source.get_tree(sha)
    parsed = parse_trace(trace_text)
    frames = resolve_frames(parsed, tree)
    bundle = await fetch_contexts(source, sha, frames, max_frames=3, radius=20)
    return source, sha, parsed, frames, bundle


async def test_bug1_blame_finds_refactor_commit(demo_repo, traces_dir):
    source, sha, parsed, frames, bundle = await _prepare(demo_repo, (traces_dir / "demo_bug1_keyerror.txt").read_text())
    assert frames[0].file == "shop/profile.py" and frames[0].resolved
    assert [c.file for c in bundle.contexts] == ["shop/profile.py", "shop/__main__.py", "shop/__main__.py"]
    assert bundle.contexts[0].highlight_line == 24
    assert not bundle.line_mismatch
    res = await find_suspect(source, sha, parsed, frames, bundle, "python", now=NOW)
    assert res.commit is not None
    assert res.commit.message.startswith("refactor(api): rename user fields")
    assert res.commit.selection == "blame"
    assert res.blamed_error_line and res.recent
    # Patches of the commit's other files must be included: the real change is in shop/api.py
    assert res.commit.file == "shop/profile.py"
    assert "+++ b/shop/api.py" in res.commit.patch and '"email_address": user.email' in res.commit.patch
    assert res.commit.patch.index("shop/profile.py") < res.commit.patch.index("shop/api.py")


async def test_bug2_related_commit_in_other_file(demo_repo, traces_dir):
    source, sha, parsed, frames, bundle = await _prepare(demo_repo, (traces_dir / "demo_bug2_null_name.txt").read_text())
    assert bundle.contexts[0].file == "scripts/greet.js"
    res = await find_suspect(source, sha, parsed, frames, bundle, "javascript", now=NOW)
    assert res.commit is not None
    assert res.commit.message.startswith("feat(users): hide inactive users")
    assert res.commit.selection == "related"
    assert res.commit.file == "scripts/users.js"
    assert "return null" in res.commit.patch


async def test_bug3_related_commit_date_format(demo_repo, traces_dir):
    source, sha, parsed, frames, bundle = await _prepare(
        demo_repo, (traces_dir / "demo_bug3_date_format.txt").read_text()
    )
    assert bundle.contexts[0].file == "shop/reports.py" and bundle.contexts[0].highlight_line == 10
    res = await find_suspect(source, sha, parsed, frames, bundle, "python", now=NOW)
    assert res.commit is not None
    assert res.commit.message.startswith("feat(api): use ISO 8601")
    assert res.commit.file == "shop/dates.py"
    assert res.commit.selection == "related"
    assert "'%Y-%m-%d'" in res.commit.selection_reason


async def test_older_ref_line_mismatch_warning(demo_repo, traces_dir):
    """Analyzing the trace against the initial commit: a differing line content triggers a warning."""
    source = LocalGitSource(parse_repo_url(str(demo_repo), allow_local=True))
    _, sha = await source.resolve_ref("HEAD~3")
    tree, _ = await source.get_tree(sha)
    parsed = parse_trace((traces_dir / "demo_bug1_keyerror.txt").read_text())
    frames = resolve_frames(parsed, tree)
    bundle = await fetch_contexts(source, sha, frames)
    # Line 24 is the same in both commits, and so are the __main__ lines -> no mismatch
    assert not bundle.line_mismatch


async def test_line_mismatch_detected(demo_repo):
    source = LocalGitSource(parse_repo_url(str(demo_repo), allow_local=True))
    _, sha = await source.resolve_ref(None)
    tree, _ = await source.get_tree(sha)
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/app/shop/profile.py", line 5, in display_name\n'
        "    a completely different line\n"
        "KeyError: 'x'\n"
    )
    parsed = parse_trace(trace)
    frames = resolve_frames(parsed, tree)
    bundle = await fetch_contexts(source, sha, frames)
    assert bundle.line_mismatch
    assert any("differs from the line" in w for w in bundle.warnings)


def test_signature_scoring():
    parsed = parse_trace(
        "Traceback (most recent call last):\n"
        '  File "/a/r.py", line 1, in f\n'
        '    return datetime.strptime(value, "%Y-%m-%d").date()\n'
        "ValueError: unconverted data remains: T14:31:05\n"
    )
    from app.context import ContextBundle
    from app.models import CodeContext

    bundle = ContextBundle(
        contexts=[
            CodeContext(
                file="r.py", start_line=1, end_line=1, highlight_line=1,
                snippet='    return datetime.strptime(value, "%Y-%m-%d").date()',
            )
        ],
        files={},
    )
    sig = ErrorSignature.build(parsed, [], bundle)
    score, hits = sig.score_patch('@@ -1 +1 @@\n-    return dt.strftime("%Y-%m-%d")\n+    return dt.isoformat()\n')
    assert score >= 3 and "'%Y-%m-%d'" in hits
    score2, _ = sig.score_patch("@@ -1 +1 @@\n-x = 1\n+x = 2\n")
    assert score2 == 0
