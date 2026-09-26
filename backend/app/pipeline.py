"""Analysis pipeline: a single async generator where every step emits a StepEvent.

parse → resolve_paths → fetch_context → blame → analyze → build_fix → done

When a step fails, the pipeline carries on as far as it can; only parse errors and user
input errors (invalid repository URL, repository/ref not found) stop it.
The final event (`done`) carries the full result in `data.result`.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, TypeVar

from app import confidence as conf
from app.analyzer import AnalyzerResult, LLMClient, LLMError, build_user_message, run_analysis
from app.config import Settings
from app.context import (
    ContextBundle,
    SuspectResult,
    fetch_contexts,
    find_suspect,
    humanize_age,
    resolve_frames,
)
from app.github_client import (
    InvalidRepoUrl,
    RefNotFound,
    RepoNotFound,
    RepoSource,
    SourceError,
    make_source,
    parse_repo_url,
)
from app.models import (
    Analysis,
    AnalysisResult,
    AnalyzeRequest,
    CodeContext,
    Fix,
    Frame,
    Meta,
    StepEvent,
)
from app.parser import ParsedTrace, TraceParseError, parse_trace
from app.storage import Storage, make_cache_key

T = TypeVar("T")

FATAL_SOURCE_ERRORS = (InvalidRepoUrl, RepoNotFound, RefNotFound)


class StepTimeout(Exception):
    pass


async def _with_timeout(aw: Awaitable[T], seconds: float, what: str) -> T:
    try:
        return await asyncio.wait_for(aw, timeout=seconds)
    except asyncio.TimeoutError as exc:
        raise StepTimeout(f"{what} did not finish within {seconds:.0f} seconds (timeout).") from exc


def new_id() -> str:
    return "an_" + secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ev(step: str, status: str, label: str, **data: Any) -> StepEvent:
    return StepEvent(step=step, status=status, label=label, data=data)  # type: ignore[arg-type]


@dataclass
class PipelineDeps:
    settings: Settings
    storage: Optional[Storage] = None
    llm_factory: Optional[Callable[[], Optional[LLMClient]]] = None
    source_factory: Callable[[str, Settings], RepoSource] = field(default=make_source)


@dataclass
class _State:
    parsed: Optional[ParsedTrace] = None
    frames: list[Frame] = field(default_factory=list)
    contexts: list[CodeContext] = field(default_factory=list)
    bundle: Optional[ContextBundle] = None
    suspect: Optional[SuspectResult] = None
    analysis: Optional[Analysis] = None
    fix: Optional[Fix] = None
    fix_unavailable_reason: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    ref: str = ""
    sha: Optional[str] = None
    repo_label: str = ""
    model: Optional[str] = None


def _frames_from_parsed(parsed: ParsedTrace) -> list[Frame]:
    return [
        Frame(
            file=f.file,
            raw_file=f.file,
            line=f.line,
            column=f.column,
            function=f.function,
            source=f.source,
            is_user_code=f.is_user_code,
            resolved=False,
        )
        for f in parsed.frames
    ]


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def run_pipeline(req: AnalyzeRequest, deps: PipelineDeps) -> AsyncIterator[StepEvent]:
    s = deps.settings
    started = time.monotonic()
    st = _State()

    # ------------------------------------------------------------------ parse
    yield ev("parse", "running", "Parsing the trace")
    try:
        parsed = parse_trace(req.trace)
    except TraceParseError as exc:
        yield ev("parse", "error", str(exc))
        yield ev("done", "error", str(exc), code="parse_error")
        return
    st.parsed = parsed
    st.frames = _frames_from_parsed(parsed)
    st.warnings.extend(parsed.warnings)
    n_user = len(parsed.user_frames)
    lang_name = "Python" if parsed.language == "python" else "JavaScript"
    first_msg_line = parsed.error.message.splitlines()[0] if parsed.error.message else ""
    yield ev(
        "parse",
        "done",
        f"{lang_name} · {parsed.error.type}{': ' + first_msg_line[:80] if first_msg_line else ''} · "
        f"{len(parsed.frames)} frames, {n_user} in user code",
        error=parsed.error.model_dump(),
        causes=[c.model_dump() for c in parsed.causes],
        frames=[f.model_dump() for f in st.frames],
        warnings=parsed.warnings,
    )

    # ------------------------------------------------------------------ resolve_paths
    yield ev("resolve_paths", "running", "Resolving the ref and fetching the repository tree")
    source: Optional[RepoSource] = None
    try:
        repo = parse_repo_url(req.repo_url, allow_local=s.errlens_allow_local_repos)
        st.repo_label = repo.full_name if not repo.local_path else f"local/{repo.name}"
        ref_input = req.ref or repo.ref
        source = deps.source_factory(req.repo_url, s)
        st.ref, st.sha = await _with_timeout(source.resolve_ref(ref_input), s.github_timeout * 2, "Ref resolution")
        if not ref_input:
            st.warnings.append(
                f"No ref given, so the default branch ({st.ref}) was used. If the trace comes from a different "
                "version, line numbers may not match."
            )
    except FATAL_SOURCE_ERRORS as exc:
        yield ev("resolve_paths", "error", str(exc))
        yield ev("done", "error", str(exc), code=exc.code)
        if source:
            await source.aclose()
        return
    except (SourceError, StepTimeout) as exc:
        st.warnings.append(f"Could not access the repository: {exc}")
        yield ev("resolve_paths", "error", f"Could not access the repository: {exc}")
        if source:
            await source.aclose()
        source = None

    cache_key: Optional[str] = None
    try:
        if source and st.sha:
            cache_key = make_cache_key(req.trace, st.repo_label, st.sha)
            if deps.storage:
                cached = await deps.storage.find_cached(cache_key)
                if cached:
                    async for e in _emit_cached(cached, st, started):
                        yield e
                    return

            try:
                tree, truncated = await _with_timeout(source.get_tree(st.sha), s.github_timeout * 2, "Repository tree")
                if truncated:
                    st.warnings.append("GitHub truncated the repository tree because it is very large; some paths may not resolve.")
                st.frames = resolve_frames(parsed, tree)
                user = [f for f in st.frames if f.is_user_code]
                resolved = [f for f in user if f.resolved]
                missing = [f.raw_file for f in user if not f.resolved]
                if missing:
                    uniq = _dedupe(missing)
                    st.warnings.append(
                        "Files not found in the repository: " + ", ".join(uniq[:4]) + (" ..." if len(uniq) > 4 else "")
                    )
                label = f"{st.ref} → {st.sha[:7]} · {len(resolved)}/{len(user)} user frames found in the repository"
                yield ev(
                    "resolve_paths",
                    "done" if resolved or not user else "error",
                    label if resolved or not user else label + " (paths did not match)",
                    ref=st.ref,
                    sha=st.sha,
                    frames=[f.model_dump() for f in st.frames],
                    missing=_dedupe(missing),
                )
            except (SourceError, StepTimeout) as exc:
                yield ev("resolve_paths", "error", f"Could not fetch the repository tree: {exc}")
                st.warnings.append(f"Could not fetch the repository tree: {exc}")

        # -------------------------------------------------------------- fetch_context
        if source and st.sha and any(f.resolved for f in st.frames):
            yield ev("fetch_context", "running", "Fetching the related code")
            try:
                bundle = await _with_timeout(
                    fetch_contexts(source, st.sha, st.frames, s.max_context_frames, s.snippet_radius),
                    s.github_timeout * 2,
                    "Fetching code",
                )
                st.bundle = bundle
                st.contexts = bundle.contexts
                st.warnings.extend(bundle.warnings)
                n_files = len({c.file for c in bundle.contexts})
                status = "done" if bundle.contexts else "error"
                label = (
                    f"Fetched {len(bundle.contexts)} snippets from {n_files} file{'s' if n_files != 1 else ''} "
                    f"(±{s.snippet_radius} lines)"
                    if bundle.contexts
                    else "Could not fetch any code"
                )
                yield ev("fetch_context", status, label, contexts=[c.model_dump() for c in bundle.contexts])
            except (SourceError, StepTimeout) as exc:
                yield ev("fetch_context", "error", f"Could not fetch code: {exc}")
                st.warnings.append(f"Could not fetch code: {exc}")
        else:
            yield ev("fetch_context", "skipped", "No user frame resolved in the repository, so code context was skipped")

        # -------------------------------------------------------------- blame
        if source and st.sha and st.bundle and st.bundle.contexts:
            yield ev("blame", "running", "Looking for the commit that changed the error line")
            try:
                sres = await _with_timeout(
                    find_suspect(
                        source,
                        st.sha,
                        parsed,
                        st.frames,
                        st.bundle,
                        parsed.language,
                        blame_radius=s.blame_radius,
                        recent_days=s.recent_days,
                    ),
                    s.github_timeout * 3,
                    "Blame",
                )
                st.suspect = sres
                if sres.commit:
                    c = sres.commit
                    age = humanize_age(c.date)
                    if c.selection == "blame":
                        target = "the error line" if sres.blamed_error_line else "a line in the call chain"
                        label = f"Commit {c.sha[:7]} changed {target} {age}"
                    else:
                        label = f"Related commit: {c.sha[:7]} changed {c.file} {age}"
                    yield ev("blame", "done", label, suspect_commit=c.model_dump())
                else:
                    yield ev("blame", "skipped", sres.skipped_reason or "No suspect commit found")
                    st.warnings.extend(sres.warnings[:2])
            except (SourceError, StepTimeout) as exc:
                yield ev("blame", "error", f"Could not blame: {exc}")
                st.warnings.append(f"Could not blame: {exc}")
        else:
            yield ev("blame", "skipped", "Blame skipped because there is no code context")
    finally:
        if source:
            await source.aclose()

    # ------------------------------------------------------------------ analyze
    llm = deps.llm_factory() if deps.llm_factory else None
    ares: Optional[AnalyzerResult] = None
    if llm is None:
        msg = "No LLM configured (set ANTHROPIC_API_KEY or GEMINI_API_KEY); analysis skipped"
        yield ev("analyze", "skipped", msg)
        st.fix_unavailable_reason = msg
    else:
        st.model = llm.model
        yield ev("analyze", "running", f"The model is analyzing ({llm.model})")
        note_markers = ("not found in the repository", "do not match", "differs from the line", "minified")
        notes = [w for w in st.warnings if any(m in w for m in note_markers)]
        user_msg = build_user_message(
            req.trace,
            parsed.error,
            parsed.causes,
            st.frames,
            st.contexts,
            st.suspect.commit if st.suspect else None,
            st.sha,
            notes,
        )
        try:
            files = st.bundle.files if st.bundle else {}
            ares = await _with_timeout(
                run_analysis(llm, user_msg, st.contexts, files, s.errlens_output_language),
                s.llm_timeout * 2 + 10,
                "LLM analysis",
            )
            retry = " (after 1 retry)" if ares.attempts > 1 else ""
            yield ev(
                "analyze",
                "done",
                f"Root cause hypothesis ready · model confidence {round(ares.output.confidence * 100)}%{retry}",
                summary=ares.output.summary,
                root_cause=ares.output.root_cause,
            )
        except (LLMError, StepTimeout) as exc:
            yield ev("analyze", "error", str(exc))
            st.fix_unavailable_reason = str(exc)
            st.warnings.append(str(exc))

    # ------------------------------------------------------------------ build_fix
    if ares is not None:
        yield ev("build_fix", "running", "Turning the fix into a diff and computing the confidence score")
        out = ares.output
        if ares.built_fix and out.fix:
            st.fix = Fix(
                file=out.fix.file,
                start_line=out.fix.start_line,
                end_line=out.fix.end_line,
                new_code=ares.built_fix.new_code,
                explanation=out.fix.explanation,
                diff=ares.built_fix.diff,
            )
        else:
            st.fix_unavailable_reason = ares.fix_unavailable_reason
        signals = conf.compute_signals(
            conf.SignalInput(
                error=parsed.error,
                frames=st.frames,
                contexts=st.contexts,
                suspect=st.suspect.commit if st.suspect else None,
                suspect_recent=bool(st.suspect and st.suspect.recent),
                fix_valid=st.fix is not None,
                line_mismatch=bool(st.bundle and st.bundle.line_mismatch),
                recent_days=s.recent_days,
            )
        )
        final, _signal_score = conf.blend(out.confidence, signals)
        st.analysis = Analysis(
            summary=out.summary,
            root_cause=out.root_cause,
            evidence=out.evidence,
            confidence=final,
            confidence_reasons=conf.reasons(out.confidence, signals),
            llm_confidence=round(out.confidence, 2),
            signals=signals,
        )
        if st.fix:
            yield ev(
                "build_fix",
                "done",
                f"Diff generated for {st.fix.file} · confidence {round(final * 100)}%",
                fix=st.fix.model_dump(),
                analysis=st.analysis.model_dump(),
            )
        else:
            yield ev(
                "build_fix",
                "skipped",
                f"No fix produced: {st.fix_unavailable_reason} · confidence {round(final * 100)}%",
                analysis=st.analysis.model_dump(),
            )
    else:
        yield ev("build_fix", "skipped", "No fix, because there is no analysis")

    # ------------------------------------------------------------------ done
    duration = int((time.monotonic() - started) * 1000)
    result = AnalysisResult(
        id=new_id(),
        created_at=_now_iso(),
        error=parsed.error,
        causes=parsed.causes,
        frames=st.frames,
        contexts=st.contexts,
        suspect_commit=st.suspect.commit if st.suspect else None,
        analysis=st.analysis,
        fix=st.fix,
        meta=Meta(
            repo=st.repo_label or req.repo_url,
            ref=st.ref or (req.ref or ""),
            sha=st.sha,
            cached=False,
            duration_ms=duration,
            model=st.model,
            warnings=_dedupe(st.warnings),
            fix_unavailable_reason=None if st.fix else st.fix_unavailable_reason,
        ),
    )
    if deps.storage:
        try:
            await deps.storage.save(result, cache_key if st.analysis else None)
        except Exception as exc:  # a storage failure must not break the analysis
            result.meta.warnings.append(f"Could not save the result: {exc}")
    yield ev("done", "done", f"Analysis complete ({duration / 1000:.1f} s)", result=result.model_dump())


async def _emit_cached(cached: AnalysisResult, st: _State, started: float) -> AsyncIterator[StepEvent]:
    yield ev(
        "resolve_paths",
        "done",
        f"{st.ref} → {(st.sha or '')[:7]} · found a saved analysis for the same trace and commit",
        ref=st.ref,
        sha=st.sha,
        frames=[f.model_dump() for f in cached.frames],
    )
    for step in ("fetch_context", "blame", "analyze", "build_fix"):
        yield ev(step, "skipped", "Served from cache")
    duration = int((time.monotonic() - started) * 1000)
    result = cached.model_copy(
        update={"meta": cached.meta.model_copy(update={"cached": True, "duration_ms": duration})}
    )
    yield ev("done", "done", f"Returned from cache ({duration / 1000:.1f} s)", result=result.model_dump())


async def run_to_completion(req: AnalyzeRequest, deps: PipelineDeps) -> tuple[Optional[AnalysisResult], list[StepEvent]]:
    events: list[StepEvent] = []
    async for e in run_pipeline(req, deps):
        events.append(e)
    last = events[-1]
    if last.step == "done" and last.status == "done":
        return AnalysisResult.model_validate(last.data["result"]), events
    return None, events
