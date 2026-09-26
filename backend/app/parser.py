"""Stack trace parsing (Python and JavaScript / Node V8).

The parser only parses text and returns raw paths. Mapping them to repository
paths needs the repository tree, so that happens in context.py.

Frames are returned innermost-first for both languages: frames[0] is where the
exception was raised.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.models import ErrorInfo, Language


class TraceParseError(ValueError):
    """The trace was not recognized or could not be parsed."""


@dataclass
class RawFrame:
    file: str
    line: int
    function: Optional[str] = None
    column: Optional[int] = None
    source: Optional[str] = None
    is_user_code: bool = True


@dataclass
class ExceptionBlock:
    type: str
    message: str
    frames: list[RawFrame] = field(default_factory=list)  # innermost first


@dataclass
class ParsedTrace:
    language: Language
    error: ErrorInfo
    frames: list[RawFrame]
    causes: list[ErrorInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def user_frames(self) -> list[RawFrame]:
        return [f for f in self.frames if f.is_user_code]


# --------------------------------------------------------------------------- shared

LIBRARY_MARKERS = (
    "site-packages",
    "dist-packages",
    "<frozen",
    "/lib/python",
    "/python.framework/",
    "/programs/python/",
    "node_modules",
    "node:internal",
    "<anonymous>",
)

_CARET_RE = re.compile(r"^\s*[\^~]+[\s\^~]*$")
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")


def _norm_path(path: str) -> str:
    return path.replace("\\", "/")


def is_library_path(path: str) -> bool:
    p = _norm_path(path).lower()
    if p.startswith(("node:", "internal/", "<")):
        return True
    if p.startswith(("http://", "https://")):
        return True
    return any(m in p for m in LIBRARY_MARKERS)


def normalize_trace(text: str) -> str:
    """Normalized trace for the cache key: whitespace and memory addresses are stripped."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _CARET_RE.match(line):
            continue
        line = _ADDR_RE.sub("0x?", line)
        line = " ".join(line.split())
        if line:
            out.append(line)
    return "\n".join(out)


def detect_language(text: str) -> Optional[Language]:
    if "Traceback (most recent call last)" in text or _PY_FRAME_RE.search(text):
        return "python"
    if _JS_FRAME_RE.search(text):
        return "javascript"
    return None


def parse_trace(text: str) -> ParsedTrace:
    if not text or not text.strip():
        raise TraceParseError("The trace is empty.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lang = detect_language(text)
    if lang == "python":
        return _parse_python(text)
    if lang == "javascript":
        return _parse_javascript(text)
    raise TraceParseError(
        "Trace not recognized. Supported formats: Python (\"Traceback (most recent call last)\") and "
        "JavaScript/Node (\"at fn (file:line:column)\")."
    )


# --------------------------------------------------------------------------- Python

_PY_FRAME_RE = re.compile(r'^\s*File "(?P<file>.+?)", line (?P<line>\d+)(?:, in (?P<func>.+?))?\s*$', re.M)
_PY_CHAIN_SEPARATORS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)
_PY_EXC_RE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<msg>.*))?$")


def _split_python_blocks(text: str) -> list[str]:
    blocks, current = [], []
    for line in text.split("\n"):
        if line.strip() in _PY_CHAIN_SEPARATORS:
            blocks.append("\n".join(current))
            current = []
        else:
            current.append(line)
    blocks.append("\n".join(current))
    return [b for b in blocks if _PY_FRAME_RE.search(b) or "Traceback" in b]


def _parse_python_block(block: str) -> ExceptionBlock:
    lines = block.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("Traceback (most recent call last)"):
            start = i + 1
            break

    frames: list[RawFrame] = []
    i = start
    last_frame_end = start
    while i < len(lines):
        m = _PY_FRAME_RE.match(lines[i])
        if m:
            frame = RawFrame(
                file=m.group("file"),
                line=int(m.group("line")),
                function=(m.group("func") or None),
            )
            j = i + 1
            # Source line and the 3.11+ marker lines (^^^ / ~~~)
            while j < len(lines) and lines[j].startswith((" ", "\t")) and not _PY_FRAME_RE.match(lines[j]):
                if not _CARET_RE.match(lines[j]) and frame.source is None and lines[j].strip():
                    frame.source = lines[j].strip()
                j += 1
            frame.is_user_code = not is_library_path(frame.file)
            frames.append(frame)
            i = j
            last_frame_end = j
            continue
        i += 1

    exc_type, message = "UnknownError", ""
    rest = [ln for ln in lines[last_frame_end:]]
    exc_idx = None
    for k, line in enumerate(rest):
        if line.strip() and not line.startswith((" ", "\t")) and not _CARET_RE.match(line):
            if _PY_EXC_RE.match(line.strip()):
                exc_idx = k
                break
    if exc_idx is not None:
        m = _PY_EXC_RE.match(rest[exc_idx].strip())
        assert m
        exc_type = m.group("type")
        msg_lines = [m.group("msg") or ""]
        for extra in rest[exc_idx + 1 :]:
            msg_lines.append(extra.rstrip())
        message = "\n".join(msg_lines).strip()
    elif not frames:
        raise TraceParseError("No frame or exception line found in the Python trace.")

    frames.reverse()  # innermost first
    return ExceptionBlock(type=exc_type, message=message, frames=frames)


def _parse_python(text: str) -> ParsedTrace:
    blocks = [_parse_python_block(b) for b in _split_python_blocks(text)]
    if not blocks:
        raise TraceParseError("Could not parse the Python trace.")
    main = blocks[-1]
    if not main.frames:
        raise TraceParseError("No frames found in the trace.")
    warnings = []
    if not any(f.is_user_code for f in main.frames):
        warnings.append("Every frame in the trace is library code; the root cause can only be analyzed to a limited extent.")
    return ParsedTrace(
        language="python",
        error=ErrorInfo(type=main.type, message=main.message, language="python"),
        frames=main.frames,
        causes=[ErrorInfo(type=b.type, message=b.message, language="python") for b in blocks[:-1]],
        warnings=warnings,
    )


# --------------------------------------------------------------------------- JavaScript

_JS_FRAME_RE = re.compile(r"^\s+at\s+(?P<body>.+?)\s*$", re.M)
_JS_LOC_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?$")
_JS_EXC_RE = re.compile(
    r"^(?:Uncaught\s+(?:\(in promise\)\s+)?)?"
    r"(?P<type>[A-Za-z_$][\w$.]*(?: \[[A-Z0-9_]+\])?)(?::\s?(?P<msg>.*))?$"
)
_HASHED_BUNDLE_RE = re.compile(r"[.-][0-9a-f]{6,}\.(?:m?js|cjs)$|\.min\.js$|/(?:dist|build|\.next)/")


def _clean_js_path(path: str) -> str:
    if path.startswith("file://"):
        path = path[len("file://") :]
        if re.match(r"^/[A-Za-z]:/", path):  # file:///C:/...
            path = path[1:]
    return path


def _parse_js_frame(body: str) -> Optional[RawFrame]:
    func: Optional[str] = None
    loc = body
    m = re.match(r"^(?P<func>.+?) \((?P<loc>.+)\)$", body)
    if m:
        func, loc = m.group("func"), m.group("loc")
    if func and func.startswith("async "):
        func = func[len("async ") :]
    if loc.startswith("async "):
        loc = loc[len("async ") :]
    lm = _JS_LOC_RE.match(loc)
    if not lm:
        return None  # frames without a location, e.g. "native", "<anonymous>", "index 2"
    file = _clean_js_path(lm.group("file"))
    return RawFrame(
        file=file,
        line=int(lm.group("line")),
        column=int(lm.group("col")) if lm.group("col") else None,
        function=func,
        is_user_code=not is_library_path(file),
    )


def _looks_minified(frames: list[RawFrame]) -> bool:
    for f in frames:
        p = _norm_path(f.file)
        if p.startswith(("http://", "https://")):
            return True
        if f.column and f.column > 300:
            return True
        if _HASHED_BUNDLE_RE.search(p):
            return True
    return False


def _parse_javascript(text: str) -> ParsedTrace:
    lines = text.split("\n")
    frame_idx = [i for i, ln in enumerate(lines) if re.match(r"^\s+at\s+", ln)]
    if not frame_idx:
        raise TraceParseError("No 'at ...' frame lines found in the JavaScript trace.")
    first = frame_idx[0]

    # Error line: the unindented "Type: message" line before the first frame.
    exc_type, message = "Error", ""
    candidates = []
    for i in range(first):
        ln = lines[i]
        if not ln.strip() or ln.startswith((" ", "\t")) or _CARET_RE.match(ln):
            continue
        s = ln.strip()
        if s.startswith(("file:", "/", "node:", "http")) or re.match(r"^[A-Za-z]:[\\/]", s):
            continue
        m = _JS_EXC_RE.match(s)
        if m:
            candidates.append((i, m))
    preferred = [c for c in candidates if re.search(r"(Error|Exception|Rejection)(\s\[|$)", c[1].group("type"))]
    chosen = (preferred or candidates or [None])[-1]
    if chosen:
        idx, m = chosen
        exc_type = m.group("type")
        msg_lines = [m.group("msg") or ""]
        for extra in lines[idx + 1 : first]:
            if extra.strip():
                msg_lines.append(extra.strip())
        message = "\n".join(msg_lines).strip()

    frames = []
    for i in frame_idx:
        body = _JS_FRAME_RE.match(lines[i])
        if body:
            fr = _parse_js_frame(body.group("body"))
            if fr:
                frames.append(fr)
    if not frames:
        raise TraceParseError("No frame with a location found in the JavaScript trace.")

    warnings = []
    if _looks_minified(frames):
        warnings.append(
            "This trace looks like minified/bundled JavaScript. Without a source map, lines and "
            "columns cannot be mapped back to the source, so results may be unreliable. "
            "If possible, use a trace from a development build or one with source maps applied."
        )
        for f in frames:
            if _norm_path(f.file).startswith(("http://", "https://")):
                f.is_user_code = False
    if not any(f.is_user_code for f in frames):
        warnings.append("Every frame in the trace is library code; the root cause can only be analyzed to a limited extent.")

    return ParsedTrace(
        language="javascript",
        error=ErrorInfo(type=exc_type, message=message, language="javascript"),
        frames=frames,
        warnings=warnings,
    )
