"""Confidence score: a blend of the LLM's own confidence and deterministic signals.

final = 0.6 × llm_confidence + 0.4 × signal_score

The signal score is the average of the applicable signals. Each signal is shown to the
user as a readable reason sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.context import humanize_age
from app.models import CodeContext, ConfidenceSignal, ErrorInfo, Frame, SuspectCommit

LLM_WEIGHT = 0.6
SIGNAL_WEIGHT = 0.4


@dataclass
class SignalInput:
    error: ErrorInfo
    frames: list[Frame]
    contexts: list[CodeContext]
    suspect: Optional[SuspectCommit]
    suspect_recent: bool
    fix_valid: bool
    line_mismatch: bool
    recent_days: int = 14


def _highlight_line(ctx: CodeContext) -> str:
    lines = ctx.snippet.splitlines()
    i = ctx.highlight_line - ctx.start_line
    return lines[i] if 0 <= i < len(lines) else ""


def _short_type(t: str) -> str:
    return t.rsplit(".", 1)[-1].split(" ")[0]


def error_line_compatible(error: ErrorInfo, line: str) -> Optional[tuple[bool, str]]:
    """Does the error type fit the code on the line? Returns None when no rule applies."""
    t = _short_type(error.type)
    msg = error.message
    code = line.strip()
    if not code:
        return None

    if t in ("KeyError", "IndexError"):
        ok = "[" in code
        return ok, (
            f"The line has a [...] access, consistent with {t}"
            if ok
            else f"The line has no [...] access, which {t} would need"
        )

    if error.language == "javascript" and t == "TypeError":
        m = re.search(r"reading '([^']+)'", msg)
        if m:
            prop = m.group(1)
            ok = bool(re.search(r"\.\s*" + re.escape(prop) + r"\b|\[\s*['\"`]" + re.escape(prop), code))
            return ok, (
                f"The line reads the '.{prop}' property" if ok else f"No '.{prop}' access is visible on the line"
            )
        if "is not a function" in msg:
            ok = "(" in code
            return ok, "The line contains a function call" if ok else "The line has no function call"
        return None

    if t == "AttributeError":
        m = re.search(r"attribute '([^']+)'", msg)
        prop = m.group(1) if m else None
        ok = bool(re.search(r"\.\s*" + re.escape(prop) + r"\b", code)) if prop else "." in code
        return ok, (
            f"The line accesses '.{prop}', consistent with AttributeError" if ok else "No matching attribute access on the line"
        )

    if t in ("NameError", "ReferenceError", "UnboundLocalError"):
        m = re.search(r"'([^']+)'", msg) or re.search(r"^(\w+) is not defined", msg)
        name = m.group(1) if m else None
        if not name:
            return None
        ok = bool(re.search(r"\b" + re.escape(name) + r"\b", code))
        return ok, f"'{name}' is used on the line" if ok else f"'{name}' does not appear on the line"

    if t == "ZeroDivisionError":
        ok = "/" in code or "%" in code
        return ok, "The line contains a division" if ok else "The line has no division"

    if t in ("ValueError", "TypeError", "JSONDecodeError"):
        ok = "(" in code or "[" in code or any(op in code for op in ("+", "*", "-"))
        return ok, f"The line has a call/operation that can raise {t}" if ok else f"The line has no call or operation for {t}"

    return None


def compute_signals(inp: SignalInput) -> list[ConfidenceSignal]:
    signals: list[ConfidenceSignal] = []

    # 1. Is the error frame in user code, and was it resolved in the repository?
    first_user = next((i for i, f in enumerate(inp.frames) if f.is_user_code), None)
    if first_user is None:
        signals.append(ConfidenceSignal(name="user_frame", score=0.0, reason="The trace has no user-code frame"))
    else:
        f = inp.frames[first_user]
        if not f.resolved:
            signals.append(
                ConfidenceSignal(
                    name="user_frame", score=0.0, reason=f"The error frame ({f.raw_file}) was not found in the repository"
                )
            )
        elif first_user == 0:
            signals.append(
                ConfidenceSignal(name="user_frame", score=1.0, reason=f"The error line is in user code ({f.file}:{f.line})")
            )
        else:
            signals.append(
                ConfidenceSignal(
                    name="user_frame",
                    score=0.75,
                    reason=f"Raised inside a library; the calling user code was found ({f.file}:{f.line})",
                )
            )

    # 2. Did the line (or related code) change within the last N days?
    if inp.suspect is None:
        signals.append(
            ConfidenceSignal(name="recent_change", score=0.0, reason="No suspect commit found (blame unavailable)")
        )
    else:
        age = humanize_age(inp.suspect.date)
        where = "The code around the error line" if inp.suspect.selection == "blame" else "Related code"
        if inp.suspect_recent:
            signals.append(
                ConfidenceSignal(
                    name="recent_change",
                    score=1.0,
                    reason=f"{where} changed {age} ({inp.suspect.sha[:7]})",
                )
            )
        else:
            signals.append(
                ConfidenceSignal(
                    name="recent_change",
                    score=0.0,
                    reason=f"No related change in the last {inp.recent_days} days (last change: {age or 'unknown'})",
                )
            )

    # 3. Does the error type fit the code on the line?
    if inp.contexts:
        compat = error_line_compatible(inp.error, _highlight_line(inp.contexts[0]))
        if compat is not None:
            ok, why = compat
            signals.append(ConfidenceSignal(name="type_match", score=1.0 if ok else 0.0, reason=why))

    # 4. Was a fix produced, and did it pass validation?
    signals.append(
        ConfidenceSignal(
            name="fix_valid",
            score=1.0 if inp.fix_valid else 0.0,
            reason="The proposed fix passed validation" if inp.fix_valid else "No validated fix could be produced",
        )
    )

    # 5. Do the trace lines match the code at this commit?
    if inp.line_mismatch:
        signals.append(
            ConfidenceSignal(
                name="line_match",
                score=0.0,
                reason="The lines in the trace do not match the analyzed commit; make sure the right ref was given",
            )
        )
    return signals


def blend(llm_confidence: float, signals: list[ConfidenceSignal]) -> tuple[float, float]:
    """Returns (final, signal_score)."""
    llm = min(1.0, max(0.0, llm_confidence))
    signal_score = sum(s.score for s in signals) / len(signals) if signals else 0.5
    final = LLM_WEIGHT * llm + SIGNAL_WEIGHT * signal_score
    return round(min(1.0, max(0.0, final)), 2), round(signal_score, 2)


def reasons(llm_confidence: float, signals: list[ConfidenceSignal]) -> list[str]:
    out = [f"The model rated its own confidence at {round(llm_confidence * 100)}%"]
    out.extend(s.reason for s in signals)
    return out
