"""Analysis engine: prompt construction, the LLM call (tool use) and two-layer validation.

1. Schema check (Pydantic)
2. Semantic check: is fix.file one of the fetched files, is the line range inside the
   shown snippet, and can the fix actually be applied?

If a check fails, the error is sent back to the model as a tool_result and the model gets
one more attempt. If that fails too, the result is returned without a fix.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.fix import BuiltFix, FixError, build_fix
from app.models import CodeContext, ErrorInfo, Frame, SuspectCommit

TOOL_NAME = "report_analysis"

TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Report the result of the error analysis in a structured form.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Plain-language summary of what went wrong, in 1-3 sentences.",
            },
            "root_cause": {
                "type": "string",
                "description": "Root cause hypothesis: why the error happens and which change/code causes it.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete evidence for the hypothesis. Start each item with 'file:line ...' or 'commit ...'.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence in the root cause hypothesis (0-1). Keep it low when evidence is thin.",
            },
            "fix": {
                "type": ["object", "null"],
                "description": "Single-file fix. null if no safe fix can be proposed.",
                "properties": {
                    "file": {"type": "string", "description": "File to change; must be one of the files shown."},
                    "start_line": {"type": "integer", "description": "First line to replace (inclusive)."},
                    "end_line": {"type": "integer", "description": "Last line to replace (inclusive)."},
                    "new_code": {
                        "type": "string",
                        "description": "Code that replaces lines start_line..end_line. Full lines with the original "
                        "indentation, without line numbers or markers.",
                    },
                    "explanation": {"type": "string", "description": "Why the fix works."},
                },
                "required": ["file", "start_line", "end_line", "new_code", "explanation"],
            },
        },
        "required": ["summary", "root_cause", "evidence", "confidence", "fix"],
    },
}


# --------------------------------------------------------------------------- output model


class LLMFix(BaseModel):
    file: str
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    new_code: str
    explanation: str = ""

    @field_validator("end_line")
    @classmethod
    def _range(cls, v: int, info: Any) -> int:
        start = info.data.get("start_line")
        if start is not None and v < start:
            raise ValueError("end_line must not be smaller than start_line")
        return v


class LLMOutput(BaseModel):
    summary: str = Field(..., min_length=1)
    root_cause: str = Field(..., min_length=1)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0, le=1)
    fix: Optional[LLMFix] = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _percent_to_ratio(cls, v: Any) -> Any:
        # Models occasionally answer on a 0-100 scale
        if isinstance(v, (int, float)) and 1 < v <= 100:
            return v / 100
        return v


# --------------------------------------------------------------------------- LLM client


@dataclass
class LLMResponse:
    tool_input: Optional[dict[str, Any]]
    tool_use_id: Optional[str]
    assistant_content: list[dict[str, Any]]
    text: str = ""


class LLMClient(Protocol):
    model: str

    async def call(self, system: str, messages: list[dict[str, Any]]) -> LLMResponse: ...


class LLMError(Exception):
    pass


class AnthropicLLM:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        timeout: float = 90.0,
        base_url: Optional[str] = None,
        http_client: Any = None,
    ):
        import anthropic

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=2, base_url=base_url, http_client=http_client
        )
        self._anthropic = anthropic

    async def call(self, system: str, messages: list[dict[str, Any]]) -> LLMResponse:
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=messages,
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except self._anthropic.AuthenticationError as exc:
            raise LLMError("The LLM API key is invalid (ANTHROPIC_API_KEY).") from exc
        except self._anthropic.RateLimitError as exc:
            raise LLMError("LLM API rate limit reached, try again shortly.") from exc
        except self._anthropic.APITimeoutError as exc:
            raise LLMError("The LLM response timed out.") from exc
        except self._anthropic.APIError as exc:
            raise LLMError(f"LLM API error: {exc}") from exc
        content: list[dict[str, Any]] = []
        tool_input, tool_id, text = None, None, ""
        for block in resp.content:
            if block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
                if block.name == TOOL_NAME:
                    tool_input, tool_id = dict(block.input), block.id
            elif block.type == "text":
                content.append({"type": "text", "text": block.text})
                text += block.text
        return LLMResponse(tool_input=tool_input, tool_use_id=tool_id, assistant_content=content, text=text)


# --------------------------------------------------------------------------- prompt


def system_prompt(output_language: str = "English") -> str:
    return f"""You are an experienced software debugging expert. Your job is to study the given stack trace, the related code and the recent changes, find the root cause of the error, and propose a single-file fix.

Rules:
- Rely only on the code and commit information shown to you. Do not make firm claims about code you have not seen; if you must assume something, say it is an assumption.
- The line the trace points to may not be the actual cause. Study the suspect commit's patch carefully: the change that triggered the error is often there.
- If the evidence is thin, give low confidence (below 0.3). Give high confidence only when the evidence is strong and consistent.
- The fix must target one of the files shown in the "Code context" section, and only the line range shown. start_line and end_line refer to the shown line numbers (both inclusive).
- new_code is the complete replacement for the changed lines: keep the original indentation, do not add line numbers or ">>" markers, and cover only the lines that really need to change.
- The fix should remove the root cause rather than hide the symptom (for example, read the correct field instead of swallowing the error with try/except). If the root cause is in a file that is not shown, propose the smallest safe fix in a shown file that adapts to that change, and say so in the explanation.
- Always fill in the fix field. Set fix to null explicitly only when a safe fix is truly impossible with the code shown.
- Do not guess field names, formats or return values; use the actual values you see in the commit patch or the code.
- Write every text field in {output_language}. Keep code, file names and identifiers as they are.
- Report the result only through the {TOOL_NAME} tool."""


def number_snippet(ctx: CodeContext) -> str:
    lines = ctx.snippet.split("\n")
    width = len(str(ctx.end_line))
    out = []
    for i, line in enumerate(lines):
        n = ctx.start_line + i
        mark = ">>" if n == ctx.highlight_line else "  "
        out.append(f"{mark} {n:>{width}} | {line}")
    return "\n".join(out)


def _code_lang(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith((".ts", ".tsx")):
        return "typescript"
    return "javascript"


def build_user_message(
    raw_trace: str,
    error: ErrorInfo,
    causes: list[ErrorInfo],
    frames: list[Frame],
    contexts: list[CodeContext],
    suspect: Optional[SuspectCommit],
    sha: Optional[str],
    notes: list[str],
    max_trace_chars: int = 8000,
) -> str:
    trace = raw_trace.strip()
    if len(trace) > max_trace_chars:
        trace = trace[: max_trace_chars // 2] + "\n... (truncated) ...\n" + trace[-max_trace_chars // 2 :]
    parts = [
        "## Error",
        f"Language: {error.language}",
        f"Exception: {error.type}: {error.message}",
    ]
    if causes:
        parts.append("Earlier (chained) exceptions: " + "; ".join(f"{c.type}: {c.message}" for c in causes))
    parts += ["", "### Raw stack trace", "```", trace, "```", ""]

    unresolved = [f for f in frames if f.is_user_code and not f.resolved]
    parts.append(f"## Code context (commit {sha[:7] if sha else 'unknown'})")
    if not contexts:
        parts.append("No code context is available; analyze based on the trace alone and set fix to null.")
    for ctx in contexts:
        fr = frames[ctx.frame_index] if ctx.frame_index < len(frames) else None
        fn = f"{fr.function}" if fr and fr.function else "?"
        role = "innermost user frame, where the error was raised" if ctx is contexts[0] else "calling frame"
        parts += [
            f"### {ctx.file}: lines {ctx.start_line}-{ctx.end_line} (function: {fn}, {role}; "
            f"error line {ctx.highlight_line} is marked with '>>')",
            f"```{_code_lang(ctx.file)}",
            number_snippet(ctx),
            "```",
            "",
        ]
    if unresolved:
        parts.append(
            "User frames not found in the repository: " + ", ".join(f"{f.raw_file}:{f.line}" for f in unresolved[:5])
        )
        parts.append("")

    parts.append("## Suspect commit")
    if suspect:
        parts += [
            f"SHA: {suspect.sha[:7]}  Author: {suspect.author}  Date: {suspect.date}",
            f"Why it was selected: {suspect.selection_reason}",
            "Message:",
            suspect.message.strip(),
            "",
        ]
        if suspect.patch:
            parts += ["Changes in this commit (all files):", "```diff", suspect.patch, "```"]
    else:
        parts.append("No suspect commit was found (blame unavailable). Base the analysis on the code only.")
    parts.append("")

    if notes:
        parts.append("## Notes")
        parts += [f"- {n}" for n in notes]
        parts.append("")

    parts.append(
        "## Task\nFind the root cause, list your evidence, state your confidence and, if possible, propose a "
        f"single-file fix. Report the result with the {TOOL_NAME} tool."
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- validation


@dataclass
class ValidatedOutput:
    output: LLMOutput
    built_fix: Optional[BuiltFix] = None
    fix_error: Optional[str] = None


def validate_fix(fix: LLMFix, contexts: list[CodeContext], files: dict[str, str]) -> BuiltFix:
    if fix.file not in files:
        allowed = ", ".join(sorted(files)) or "none"
        raise FixError(f"fix.file '{fix.file}' is not one of the files shown. Allowed files: {allowed}.")
    ranges = [(c.start_line, c.end_line) for c in contexts if c.file == fix.file]
    if not any(s <= fix.start_line and fix.end_line <= e for s, e in ranges):
        shown = ", ".join(f"{s}-{e}" for s, e in ranges)
        raise FixError(
            f"Line range {fix.start_line}-{fix.end_line} is outside the range shown for {fix.file} ({shown})."
        )
    if not fix.new_code.strip() and fix.end_line - fix.start_line > 5:
        raise FixError("new_code is empty; deleting a large block is not a safe fix.")
    return build_fix(fix.file, files[fix.file], fix.start_line, fix.end_line, fix.new_code)


def validate(raw: Optional[dict[str, Any]], contexts: list[CodeContext], files: dict[str, str]) -> ValidatedOutput:
    """Raises ValidationError on schema errors; fix problems are reported in ValidatedOutput.fix_error."""
    if raw is None:
        raise ValueError(f"The response does not contain a {TOOL_NAME} tool call.")
    output = LLMOutput.model_validate(raw)
    if output.fix is None:
        return ValidatedOutput(output=output)
    try:
        built = validate_fix(output.fix, contexts, files)
    except FixError as exc:
        return ValidatedOutput(output=output, fix_error=str(exc))
    return ValidatedOutput(output=output, built_fix=built)


def _format_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        items = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8]]
        return "Schema validation failed:\n" + "\n".join(f"- {i}" for i in items)
    return str(exc)


@dataclass
class AnalyzerResult:
    output: LLMOutput
    built_fix: Optional[BuiltFix]
    fix_unavailable_reason: Optional[str]
    attempts: int
    notes: list[str] = field(default_factory=list)


NO_FIX_REASON = "The model did not propose a safe fix."


async def run_analysis(
    llm: LLMClient,
    user_message: str,
    contexts: list[CodeContext],
    files: dict[str, str],
    output_language: str = "English",
) -> AnalyzerResult:
    system = system_prompt(output_language)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    last_error: Optional[str] = None
    best: Optional[ValidatedOutput] = None

    for attempt in (1, 2):
        resp = await llm.call(system, messages)
        try:
            validated = validate(resp.tool_input, contexts, files)
        except (ValidationError, ValueError) as exc:
            last_error = _format_validation_error(exc)
            validated = None
        if validated is not None:
            if best is None or best.built_fix is None:
                best = validated
            if validated.fix_error is None:
                if validated.output.fix is not None:
                    return AnalyzerResult(validated.output, validated.built_fix, None, attempt)
                # With code context available, ask explicitly once (some models leave the field empty lazily)
                if attempt == 2 or not contexts:
                    return AnalyzerResult(validated.output, None, NO_FIX_REASON, attempt)
                last_error = (
                    "The fix field was left empty. Your root cause hypothesis is clear; propose the smallest fix "
                    "in one of the shown files that removes this error. If a safe fix is truly impossible, set "
                    "fix to null again and explain why in root_cause."
                )
            else:
                last_error = f"The fix could not be validated: {validated.fix_error}"

        if attempt == 1:
            feedback = (
                f"{last_error}\n\nPlease correct this and call the {TOOL_NAME} tool again. "
                "The fix must target only the files and lines shown."
            )
            # If the provider knows its own conversation format (e.g. Gemini functionResponse), use it
            append_error = getattr(llm, "append_error", None)
            if append_error is not None:
                messages = append_error(messages, resp, feedback)
                continue
            messages.append(
                {
                    "role": "assistant",
                    "content": resp.assistant_content or [{"type": "text", "text": resp.text or "(empty)"}],
                }
            )
            if resp.tool_use_id:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": resp.tool_use_id, "is_error": True, "content": feedback}
                        ],
                    }
                )
            else:
                messages.append({"role": "user", "content": feedback})

    if best is not None:
        reason = f"The proposed fix failed validation: {best.fix_error}" if best.fix_error else NO_FIX_REASON
        return AnalyzerResult(best.output.model_copy(update={"fix": None}), None, reason, 2)
    raise LLMError(f"The model did not produce a valid analysis in two attempts. {last_error or ''}".strip())


def dump_debug(result: AnalyzerResult) -> str:
    return json.dumps(result.output.model_dump(), ensure_ascii=False, indent=2)
