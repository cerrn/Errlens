"""ErrLens data contract.

Every API model is defined here, in one place. Frontend types are generated from
the JSON Schema of these models (see scripts/export_schema.py).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal["python", "javascript"]
StepName = Literal["parse", "resolve_paths", "fetch_context", "blame", "analyze", "build_fix", "done"]
StepStatus = Literal["running", "done", "skipped", "error"]

STEP_ORDER: list[str] = ["parse", "resolve_paths", "fetch_context", "blame", "analyze", "build_fix", "done"]


class _Model(BaseModel):
    # Fields with defaults are always present in serialized output; mark them required in TS types
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# --------------------------------------------------------------------------- input


class AnalyzeRequest(_Model):
    trace: str = Field(..., min_length=1, max_length=100_000, description="Raw stack trace")
    repo_url: str = Field(..., min_length=1, max_length=500, description="GitHub repository URL")
    ref: Optional[str] = Field(None, max_length=200, description="Branch, tag or commit SHA")

    @field_validator("trace")
    @classmethod
    def _trace_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Stack trace must not be empty")
        return v

    @field_validator("ref")
    @classmethod
    def _blank_ref_is_none(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if v is not None else None


# --------------------------------------------------------------------------- result parts


class ErrorInfo(_Model):
    type: str
    message: str
    language: Language


class Frame(_Model):
    file: str = Field(..., description="Repository path if resolved, otherwise the raw path")
    raw_file: str = Field(..., description="Raw path as it appears in the trace")
    line: int
    column: Optional[int] = None
    function: Optional[str] = None
    source: Optional[str] = Field(None, description="Source line shown under the frame in the trace")
    is_user_code: bool
    resolved: bool = False


class CodeContext(_Model):
    file: str
    start_line: int
    end_line: int
    highlight_line: int
    snippet: str
    frame_index: int = Field(0, description="Index of the related frame in the frames list")


class SuspectCommit(_Model):
    sha: str
    message: str
    author: str
    date: str
    url: str
    patch: Optional[str] = None
    file: Optional[str] = Field(None, description="File the patch belongs to")
    selection: Literal["blame", "related"] = "blame"
    selection_reason: str = ""


class ConfidenceSignal(_Model):
    name: str
    score: float = Field(..., ge=0, le=1)
    reason: str


class Analysis(_Model):
    summary: str
    root_cause: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0, le=1)
    confidence_reasons: list[str] = Field(default_factory=list)
    llm_confidence: Optional[float] = Field(None, ge=0, le=1)
    signals: list[ConfidenceSignal] = Field(default_factory=list)


class Fix(_Model):
    file: str
    start_line: int
    end_line: int
    new_code: str
    explanation: str
    diff: str


class Meta(_Model):
    repo: str
    ref: str
    sha: Optional[str] = None
    cached: bool = False
    duration_ms: int = 0
    model: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    fix_unavailable_reason: Optional[str] = None


class AnalysisResult(_Model):
    id: str
    created_at: str
    error: ErrorInfo
    causes: list[ErrorInfo] = Field(default_factory=list, description="Earlier exceptions in a chained traceback")
    frames: list[Frame]
    contexts: list[CodeContext] = Field(default_factory=list)
    suspect_commit: Optional[SuspectCommit] = None
    analysis: Optional[Analysis] = None
    fix: Optional[Fix] = None
    meta: Meta


# --------------------------------------------------------------------------- stream & history


class StepEvent(_Model):
    step: StepName
    status: StepStatus
    label: str
    data: dict[str, Any] = Field(default_factory=dict)


class HistoryItem(_Model):
    id: str
    created_at: str
    repo: str
    error_type: str
    error_message: str = ""
    confidence: Optional[float] = None


class HistoryList(_Model):
    items: list[HistoryItem]


class ErrorResponse(_Model):
    detail: str
    code: str = "error"


class HealthResponse(_Model):
    status: Literal["ok"] = "ok"
    version: str
    github_token: bool
    llm_configured: bool
