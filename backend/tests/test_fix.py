import subprocess

import pytest

from app.fix import FixError, apply_edit, build_fix, unified_diff

ORIG = "def f(d):\n    x = 1\n    return d['email']\n\n\nprint(f({}))\n"


def test_single_line_replace_and_diff_headers():
    built = build_fix("app/user.py", ORIG, 3, 3, "    return d['email_address']")
    assert "--- a/app/user.py" in built.diff and "+++ b/app/user.py" in built.diff
    assert "-    return d['email']" in built.diff
    assert "+    return d['email_address']" in built.diff
    assert built.new_content.count("\n") == ORIG.count("\n")


def test_reindent_when_model_drops_indent():
    new, code = apply_edit(ORIG, 3, 3, "return d.get('email')")
    assert "    return d.get('email')\n" in new
    assert code == "    return d.get('email')"


def test_strip_copied_line_numbers():
    new, _ = apply_edit(ORIG, 3, 3, ">>  3 |     return d.get('email')")
    assert "    return d.get('email')\n" in new


def test_multi_line_insert():
    new, _ = apply_edit(ORIG, 3, 3, "    if 'email' not in d:\n        return None\n    return d['email']")
    assert new.splitlines()[2:5] == ["    if 'email' not in d:", "        return None", "    return d['email']"]


@pytest.mark.parametrize("start,end", [(0, 1), (3, 2), (5, 99)])
def test_invalid_ranges(start, end):
    with pytest.raises(FixError):
        apply_edit(ORIG, start, end, "x")


def test_identical_code_rejected():
    with pytest.raises(FixError):
        build_fix("a.py", ORIG, 3, 3, "    return d['email']")


def test_python_syntax_error_rejected():
    with pytest.raises(FixError, match="syntax error"):
        build_fix("a.py", ORIG, 3, 3, "    return d['email'")


def test_crlf_preserved():
    crlf = ORIG.replace("\n", "\r\n")
    new, _ = apply_edit(crlf, 3, 3, "    return 1")
    assert "    return 1\r\n" in new and "\n" not in new.replace("\r\n", "")


def test_no_trailing_newline_git_apply(tmp_path):
    original = "a = 1\nb = 2"  # no trailing newline
    (tmp_path / "m.py").write_bytes(original.encode())
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    built = build_fix("m.py", original, 2, 2, "b = 3")
    assert "\\ No newline at end of file" in built.diff
    (tmp_path / "fix.diff").write_bytes(built.diff.encode())
    subprocess.run(["git", "apply", "--check", "fix.diff"], cwd=tmp_path, check=True)
    subprocess.run(["git", "apply", "fix.diff"], cwd=tmp_path, check=True)
    assert (tmp_path / "m.py").read_bytes() == b"a = 1\nb = 3"


def test_unified_diff_empty_when_same():
    assert unified_diff("x", "a\n", "a\n") == ""


def test_context_bleed_leading_line_removed():
    js = "function greeting(id) {\n  const user = findUser(id);\n  return `Hi ${user.name}`;\n}\n"
    code = "  const user = findUser(id);\n  return user ? `Hi ${user.name}` : 'Hi';"
    built = build_fix("g.js", js, 3, 3, code)
    assert built.new_content.count("const user") == 1
    assert "return user ?" in built.new_content


def test_context_bleed_trailing_line_removed():
    new, _ = apply_edit(ORIG, 3, 3, "    return d.get('email')\n\n\nprint(f({}))")
    assert new.count("print(f({}))") == 1


def test_intentional_same_line_kept():
    # Leave it alone when the replaced block itself starts with the same line
    new, _ = apply_edit(ORIG, 2, 3, "    x = 1\n    return d.get('email')")
    assert new.count("x = 1") == 1


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not installed")
def test_js_syntax_error_rejected():
    js = "function f() {\n  const a = 1;\n  return a;\n}\n"
    with pytest.raises(FixError, match="syntax error"):
        build_fix("a.js", js, 3, 3, "  return a +;")
