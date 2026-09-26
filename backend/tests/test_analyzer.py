import pytest

from app.analyzer import NO_FIX_REASON, LLMError, build_user_message, number_snippet, run_analysis
from app.models import CodeContext, ErrorInfo, Frame, SuspectCommit

from .fakes import ScriptedLLM

CONTENT = "\n".join(f"line{i}" for i in range(1, 61)) + "\n"
FILES = {"app/x.py": CONTENT}
CTX = [CodeContext(file="app/x.py", start_line=20, end_line=40, highlight_line=30,
                   snippet="\n".join(f"line{i}" for i in range(20, 41)))]


def out(fix=None, confidence=0.8):
    return {"summary": "summary", "root_cause": "cause", "evidence": ["app/x.py:30"], "confidence": confidence, "fix": fix}


def fix(file="app/x.py", start=30, end=30, code="line30_fixed"):
    return {"file": file, "start_line": start, "end_line": end, "new_code": code, "explanation": "..."}


async def test_valid_first_try():
    llm = ScriptedLLM([out(fix())])
    res = await run_analysis(llm, "msg", CTX, FILES)
    assert res.attempts == 1 and res.built_fix is not None
    assert "+line30_fixed" in res.built_fix.diff


async def test_fix_outside_snippet_retried_with_tool_result():
    llm = ScriptedLLM([out(fix(start=55, end=55)), out(fix())])
    res = await run_analysis(llm, "msg", CTX, FILES)
    assert res.attempts == 2 and res.built_fix is not None
    # The second call must carry the error as a tool_result
    _, messages = llm.calls[1]
    assert messages[1]["role"] == "assistant"
    tr = messages[2]["content"][0]
    assert tr["type"] == "tool_result" and tr["is_error"] is True
    assert "outside the range shown" in tr["content"]


async def test_wrong_file_twice_returns_without_fix():
    llm = ScriptedLLM([out(fix(file="other.py")), out(fix(file="other.py"))])
    res = await run_analysis(llm, "msg", CTX, FILES)
    assert res.built_fix is None and res.output.fix is None
    assert "failed validation" in res.fix_unavailable_reason
    assert res.output.summary == "summary"


async def test_schema_error_then_valid():
    bad = {"summary": "x", "confidence": "very"}  # missing fields
    llm = ScriptedLLM([bad, out(None)])
    res = await run_analysis(llm, "msg", CTX, FILES)
    assert res.attempts == 2 and res.built_fix is None
    assert res.fix_unavailable_reason == NO_FIX_REASON
    assert "Schema validation failed" in llm.calls[1][1][2]["content"][0]["content"]


async def test_schema_error_twice_raises():
    llm = ScriptedLLM([None, {"summary": ""}])
    with pytest.raises(LLMError):
        await run_analysis(llm, "msg", CTX, FILES)


async def test_percent_confidence_normalized():
    llm = ScriptedLLM([out(fix(), confidence=85)])
    res = await run_analysis(llm, "msg", CTX, FILES)
    assert res.output.confidence == pytest.approx(0.85)


def test_number_snippet_marks_highlight():
    s = number_snippet(CTX[0])
    assert ">> 30 | line30" in s and "   20 | line20" in s


def test_user_message_contains_everything():
    msg = build_user_message(
        "Traceback...",
        ErrorInfo(type="KeyError", message="'email'", language="python"),
        [],
        [Frame(file="app/x.py", raw_file="/srv/app/x.py", line=30, function="f", is_user_code=True, resolved=True),
         Frame(file="/tmp/y.py", raw_file="/tmp/y.py", line=3, is_user_code=True, resolved=False)],
        CTX,
        SuspectCommit(sha="a1b2c3d4e5", message="refactor", author="Ece", date="2026-09-23T10:00:00Z", url="",
                      patch="@@ -1 +1 @@\n-a\n+b", file="app/x.py", selection="blame", selection_reason="blame"),
        "f" * 40,
        ["Note 1"],
    )
    for needle in ("KeyError: 'email'", ">> 30 | line30", "a1b2c3d", "```diff", "/tmp/y.py:3", "- Note 1", "report_analysis"):
        assert needle in msg


async def test_empty_fix_asked_once_more():
    llm = ScriptedLLM([out(None), out(fix())])
    res = await run_analysis(llm, "msg", CTX, FILES)
    assert res.attempts == 2 and res.built_fix is not None
    assert "fix field was left empty" in llm.calls[1][1][2]["content"][0]["content"]


async def test_empty_fix_twice_accepted():
    llm = ScriptedLLM([out(None), out(None)])
    res = await run_analysis(llm, "msg", CTX, FILES)
    assert res.built_fix is None and res.fix_unavailable_reason == NO_FIX_REASON


async def test_empty_fix_without_context_not_retried():
    llm = ScriptedLLM([out(None)])
    res = await run_analysis(llm, "msg", [], {})
    assert res.attempts == 1 and res.built_fix is None
