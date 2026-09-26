"""ErrLens API."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.analyzer import AnthropicLLM, LLMClient
from app.config import Settings, get_settings
from app.models import (
    AnalysisResult,
    AnalyzeRequest,
    ErrorResponse,
    HealthResponse,
    HistoryList,
    StepEvent,
)
from app.pipeline import PipelineDeps, run_pipeline
from app.storage import Storage

VERSION = "0.1.0"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_result.json"

HTTP_STATUS_BY_CODE = {
    "parse_error": 422,
    "invalid_repo_url": 422,
    "repo_not_found": 404,
    "ref_not_found": 404,
}


def default_llm_factory(settings: Settings):
    def factory() -> Optional[LLMClient]:
        provider = settings.resolved_llm_provider
        if provider == "anthropic":
            return AnthropicLLM(
                settings.anthropic_api_key, settings.errlens_model, settings.errlens_max_tokens, settings.llm_timeout
            )
        if provider == "gemini":
            from app.gemini import GeminiLLM

            return GeminiLLM(
                settings.gemini_api_key,
                settings.gemini_model,
                max_output_tokens=max(8192, settings.errlens_max_tokens),
                timeout=settings.llm_timeout,
                api_url=settings.gemini_api_url,
            )
        return None

    return factory


def create_app(settings: Optional[Settings] = None, deps: Optional[PipelineDeps] = None) -> FastAPI:
    settings = settings or get_settings()
    storage = Storage(settings.errlens_db_path)
    deps = deps or PipelineDeps(settings=settings, storage=storage, llm_factory=default_llm_factory(settings))
    if deps.storage is None:
        deps.storage = storage

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await deps.storage.init()  # type: ignore[union-attr]
        yield

    app = FastAPI(title="ErrLens API", version=VERSION, lifespan=lifespan)
    app.state.deps = deps
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=r"https://.*\.vercel\.app" if "*.vercel.app" in settings.cors_origins else None,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------- helpers

    async def events_for(req: AnalyzeRequest) -> AsyncIterator[StepEvent]:
        if settings.errlens_fixture_mode:
            async for e in fixture_events():
                yield e
            return
        async for e in run_pipeline(req, deps):
            yield e

    # -------------------------------------------------------------- endpoints

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            version=VERSION,
            github_token=bool(settings.github_token),
            llm_configured=settings.resolved_llm_provider is not None or settings.errlens_fixture_mode,
        )

    @app.post(
        "/analyze",
        response_model=AnalysisResult,
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
    )
    async def analyze(req: AnalyzeRequest) -> Any:
        last: Optional[StepEvent] = None

        async def consume() -> Optional[StepEvent]:
            final = None
            async for e in events_for(req):
                final = e
            return final

        try:
            last = await asyncio.wait_for(consume(), timeout=settings.pipeline_timeout)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={"detail": f"The analysis did not finish within {settings.pipeline_timeout:.0f} seconds.", "code": "timeout"},
            )
        if last is None or last.step != "done":
            raise HTTPException(status_code=500, detail="The pipeline ended unexpectedly.")
        if last.status == "error":
            code = str(last.data.get("code", "error"))
            return JSONResponse(
                status_code=HTTP_STATUS_BY_CODE.get(code, 400), content={"detail": last.label, "code": code}
            )
        return last.data["result"]

    @app.post("/analyze/stream")
    async def analyze_stream(req: AnalyzeRequest, request: Request) -> EventSourceResponse:
        async def gen() -> AsyncIterator[dict[str, str]]:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + settings.pipeline_timeout
            agen = events_for(req).__aiter__()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    remaining = deadline - loop.time()
                    try:
                        e = await asyncio.wait_for(agen.__anext__(), timeout=max(0.1, remaining))
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        err = StepEvent(
                            step="done",
                            status="error",
                            label=f"The analysis did not finish within {settings.pipeline_timeout:.0f} seconds (timeout).",
                            data={"code": "timeout"},
                        )
                        yield {"event": "step", "data": err.model_dump_json()}
                        break
                    yield {"event": "step", "data": e.model_dump_json()}
            finally:
                await agen.aclose()

        return EventSourceResponse(
            gen(),
            ping=10,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"},
        )

    @app.get("/history", response_model=HistoryList)
    async def history(limit: int = Query(50, ge=1, le=200)) -> HistoryList:
        return HistoryList(items=await deps.storage.list(limit))  # type: ignore[union-attr]

    @app.get("/history/{analysis_id}", response_model=AnalysisResult, responses={404: {"model": ErrorResponse}})
    async def history_item(analysis_id: str) -> Any:
        result = await deps.storage.get(analysis_id)  # type: ignore[union-attr]
        if result is None:
            return JSONResponse(status_code=404, content={"detail": "Analysis not found.", "code": "not_found"})
        return result

    return app


async def fixture_events() -> AsyncIterator[StepEvent]:
    """In fixture mode, emits step events with realistic delays."""
    result = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    r = AnalysisResult.model_validate(result)
    steps = [
        ("parse", f"Python · {r.error.type}: {r.error.message.splitlines()[0][:60]}", {"error": r.error.model_dump()}),
        ("resolve_paths", f"{r.meta.ref} → {(r.meta.sha or '')[:7]} · user frames found in the repository", {}),
        ("fetch_context", f"Fetched {len(r.contexts)} snippets", {}),
        ("blame", f"Related commit: {r.suspect_commit.sha[:7] if r.suspect_commit else '-'}", {}),
        ("analyze", "Root cause hypothesis ready", {}),
        ("build_fix", "Diff generated", {}),
    ]
    running = {
        "parse": "Parsing the trace",
        "resolve_paths": "Resolving the ref and fetching the repository tree",
        "fetch_context": "Fetching the related code",
        "blame": "Looking for the commit that changed the error line",
        "analyze": "The model is analyzing (fixture)",
        "build_fix": "Turning the fix into a diff",
    }
    for step, label, data in steps:
        yield StepEvent(step=step, status="running", label=running[step])  # type: ignore[arg-type]
        await asyncio.sleep(0.5 if step != "analyze" else 1.5)
        yield StepEvent(step=step, status="done", label=label, data=data)  # type: ignore[arg-type]
    yield StepEvent(step="done", status="done", label="Analysis complete (fixture)", data={"result": result})


app = create_app()
