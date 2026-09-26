"""Turns a structured edit (file, line range, new code) into a unified diff.

The LLM never writes the diff itself: it frequently breaks hunk headers. A diff built
here with difflib always applies cleanly with `git apply`.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Optional


class FixError(ValueError):
    pass


@dataclass
class BuiltFix:
    diff: str
    new_content: str
    new_code: str


_NUMBERED_RE = re.compile(r"^\s*(?:>>)?\s*\d+\s?\| ?")


def _strip_line_numbers(code: str) -> str:
    """Strips prompt line numbers if the model copied them into its code."""
    lines = code.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    if non_empty and all(_NUMBERED_RE.match(ln) for ln in non_empty):
        return "\n".join(_NUMBERED_RE.sub("", ln, count=1) for ln in lines)
    return code


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent(new_lines: list[str], old_lines: list[str]) -> list[str]:
    """Applies the original block's indentation when the new code arrives unindented."""
    old_nonempty = [ln for ln in old_lines if ln.strip()]
    new_nonempty = [ln for ln in new_lines if ln.strip()]
    if not old_nonempty or not new_nonempty:
        return new_lines
    old_indent = min((_indent_of(ln) for ln in old_nonempty), key=len)
    new_indent = min((_indent_of(ln) for ln in new_nonempty), key=len)
    if old_indent and not new_indent:
        return [(old_indent + ln) if ln.strip() else ln for ln in new_lines]
    return new_lines


def _drop_context_bleed(new_lines: list[str], lines: list[str], start_line: int, end_line: int) -> list[str]:
    """Fixes the model copying neighbouring lines from outside the range into new_code.

    E.g. when only line 6 should change but new_code also contains line 5, line 5 would be
    written twice (in JS, `const user` gets declared twice). If the start of new_code equals
    the lines right before start_line, or its end equals the lines right after end_line,
    that part is dropped.
    """
    orig = [ln.rstrip("\r\n") for ln in lines]
    old_block = orig[start_line - 1 : end_line]
    out = list(new_lines)
    # leading bleed
    for k in range(min(len(out) - 1, start_line - 1, 5), 0, -1):
        if out[:k] == orig[start_line - 1 - k : start_line - 1] and out[:k] != old_block[:k]:
            out = out[k:]
            break
    # trailing bleed
    for k in range(min(len(out) - 1, len(orig) - end_line, 5), 0, -1):
        if out[-k:] == orig[end_line : end_line + k] and out[-k:] != old_block[-k:]:
            out = out[:-k]
            break
    return out


def apply_edit(original: str, start_line: int, end_line: int, new_code: str) -> tuple[str, str]:
    """Replaces lines [start_line, end_line] of the original. Returns (new content, normalized new code)."""
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines(keepends=True)
    n = len(lines)
    if start_line < 1 or end_line < start_line or end_line > n:
        raise FixError(f"Invalid line range {start_line}-{end_line} (the file has {n} lines).")

    code = _strip_line_numbers(new_code.replace("\r\n", "\n")).rstrip("\n")
    old_block = [ln.rstrip("\r\n") for ln in lines[start_line - 1 : end_line]]
    raw_lines = _drop_context_bleed(code.split("\n") if code else [], lines, start_line, end_line)
    new_lines = _reindent(raw_lines, old_block)

    last_had_newline = lines[end_line - 1].endswith(("\n", "\r"))
    replacement = [ln + newline for ln in new_lines]
    if replacement and not last_had_newline:
        replacement[-1] = replacement[-1][: -len(newline)]
    new_content = "".join(lines[: start_line - 1] + replacement + lines[end_line:])
    return new_content, "\n".join(new_lines)


def unified_diff(path: str, original: str, new_content: str) -> str:
    a = original.splitlines(keepends=True)
    b = new_content.splitlines(keepends=True)
    out = []
    for line in difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}", n=3):
        if line.endswith("\n"):
            out.append(line)
        else:
            # Marker git expects when the file does not end with a newline
            out.append(line + "\n")
            out.append("\\ No newline at end of file\n")
    return "".join(out)


def check_syntax(path: str, content: str) -> Optional[str]:
    """Syntax check: compile() for Python, `node --check` for JavaScript (when node is available)."""
    if path.endswith(".py"):
        try:
            compile(content, path, "exec")
        except SyntaxError as exc:
            return f"The proposed code has a syntax error: {exc.msg} (line {exc.lineno})"
        return None
    if path.endswith((".js", ".mjs", ".cjs")):
        return _node_check(path, content)
    return None


def _node_check(path: str, content: str) -> Optional[str]:
    import os
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        return None  # no node: skip the check
    suffix = os.path.splitext(path)[1]
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        proc = subprocess.run([node, "--check", tmp], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if proc.returncode == 0:
        return None
    err = next((ln.strip() for ln in proc.stderr.splitlines() if "Error" in ln), proc.stderr.strip()[:200])
    return f"The proposed code has a syntax error: {err}"


def build_fix(path: str, original: str, start_line: int, end_line: int, new_code: str) -> BuiltFix:
    new_content, normalized = apply_edit(original, start_line, end_line, new_code)
    if new_content == original:
        raise FixError("The proposed code is identical to the current code; nothing changes.")
    err = check_syntax(path, new_content)
    if err and check_syntax(path, original) is None:  # skip the check if the original does not compile either
        raise FixError(err)
    return BuiltFix(diff=unified_diff(path, original, new_content), new_content=new_content, new_code=normalized)
