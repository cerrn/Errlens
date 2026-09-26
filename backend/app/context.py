"""Code context: path resolution, snippet fetching, blame and suspect commit selection."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Optional

from app.github_client import BlameRange, CommitDetail, CommitInfo, RepoSource, SourceError
from app.models import CodeContext, Frame, Language, SuspectCommit
from app.parser import ParsedTrace, RawFrame

# --------------------------------------------------------------------------- path resolution


def _path_parts(path: str) -> list[str]:
    p = path.replace("\\", "/")
    p = re.sub(r"^[A-Za-z]:/", "/", p)  # drive letter
    return [s for s in p.split("/") if s and s != "."]


def resolve_path(raw: str, tree: list[str], index: Optional[dict[str, list[str]]] = None) -> Optional[str]:
    """Maps a trace path to a repository path using the longest suffix match in the tree.

    Returns None when several candidates tie for the longest match (ambiguous).
    """
    parts = _path_parts(raw)
    if not parts:
        return None
    if index is None:
        index = build_tree_index(tree)
    candidates = index.get(parts[-1], [])
    best: list[str] = []
    best_len = 0
    for cand in candidates:
        cparts = cand.split("/")
        n = 0
        while n < len(cparts) and n < len(parts) and cparts[-1 - n] == parts[-1 - n]:
            n += 1
        if n > best_len:
            best, best_len = [cand], n
        elif n == best_len and n > 0:
            best.append(cand)
    if len(best) == 1:
        return best[0]
    if len(best) > 1:
        # If one candidate matches the whole trace path (a relative path), pick it
        exact = [b for b in best if len(b.split("/")) == best_len]
        if len(exact) == 1:
            return exact[0]
    return None


def build_tree_index(tree: list[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for p in tree:
        index.setdefault(p.rsplit("/", 1)[-1], []).append(p)
    return index


def resolve_frames(parsed: ParsedTrace, tree: list[str]) -> list[Frame]:
    index = build_tree_index(tree)
    frames: list[Frame] = []
    for rf in parsed.frames:
        resolved = resolve_path(rf.file, tree, index) if rf.is_user_code else None
        frames.append(
            Frame(
                file=resolved or rf.file,
                raw_file=rf.file,
                line=rf.line,
                column=rf.column,
                function=rf.function,
                source=rf.source,
                is_user_code=rf.is_user_code,
                resolved=resolved is not None,
            )
        )
    return frames


# --------------------------------------------------------------------------- snippet


@dataclass
class ContextBundle:
    contexts: list[CodeContext]
    files: dict[str, str]  # path -> content
    warnings: list[str] = field(default_factory=list)
    line_mismatch: bool = False


def target_frame_indices(frames: list[Frame], max_frames: int) -> list[int]:
    """Frames to fetch context for: the innermost resolved user-code frames."""
    out, seen = [], set()
    for i, f in enumerate(frames):
        if f.is_user_code and f.resolved and (f.file, f.line) not in seen:
            out.append(i)
            seen.add((f.file, f.line))
        if len(out) >= max_frames:
            break
    return out


def make_snippet(content: str, line: int, radius: int) -> tuple[int, int, str]:
    lines = content.splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return start, end, "\n".join(lines[start - 1 : end])


def _same_code(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


async def fetch_contexts(
    source: RepoSource, sha: str, frames: list[Frame], max_frames: int = 3, radius: int = 20
) -> ContextBundle:
    idx = target_frame_indices(frames, max_frames)
    paths = sorted({frames[i].file for i in idx})
    results = await asyncio.gather(*(source.get_file(sha, p) for p in paths), return_exceptions=True)
    files: dict[str, str] = {}
    warnings: list[str] = []
    for p, r in zip(paths, results):
        if isinstance(r, BaseException):
            warnings.append(f"Could not fetch '{p}': {r}")
        else:
            files[p] = r
    contexts: list[CodeContext] = []
    mismatch = False
    for i in idx:
        f = frames[i]
        content = files.get(f.file)
        if content is None:
            continue
        n_lines = len(content.splitlines())
        if f.line > n_lines:
            warnings.append(
                f"{f.file}:{f.line} does not exist (the file has {n_lines} lines). Line numbers do not match "
                "this commit; enter the right branch/commit."
            )
            mismatch = True
            continue
        actual = content.splitlines()[f.line - 1]
        if f.source and not _same_code(f.source, actual):
            mismatch = True
            warnings.append(
                f"{f.file}:{f.line} differs from the line in the trace. The trace may come from a different commit."
            )
        start, end, snippet = make_snippet(content, f.line, radius)
        contexts.append(
            CodeContext(file=f.file, start_line=start, end_line=end, highlight_line=f.line, snippet=snippet, frame_index=i)
        )
    return ContextBundle(contexts=contexts, files=files, warnings=warnings, line_mismatch=mismatch)


# --------------------------------------------------------------------------- suspect commit

_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_STRING_RE = re.compile(r"""(?P<q>["'`])(?P<s>(?:\\.|(?!(?P=q)).){2,80}?)(?P=q)""")

STOPWORDS = {
    # Python
    "def", "return", "if", "else", "elif", "for", "while", "in", "not", "and", "or", "is", "import", "from",
    "as", "class", "self", "cls", "try", "except", "finally", "raise", "with", "pass", "lambda", "yield",
    "True", "False", "print", "str", "int", "float", "dict", "list", "len", "range", "get", "f",
    # JS
    "const", "let", "var", "function", "new", "this", "true", "false", "async", "await", "export",
    "module", "exports", "require", "console", "log", "typeof", "of",
    # general
    "value", "data", "result", "e", "i", "x", "id", "a", "b", "the", "to", "at", "line", "Error",
    # generic words from error messages
    "Cannot", "read", "properties", "reading", "object", "has", "no", "attribute", "does", "match",
    "format", "time", "remains", "unconverted", "is", "defined", "type", "NoneType", "missing",
}

LANG_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py", ".pyi"),
    "javascript": (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"),
}


def extract_tokens(text: str) -> tuple[set[str], set[str]]:
    """Extracts (identifiers, string literals) from text."""
    literals = {m.group("s") for m in _STRING_RE.finditer(text)}
    idents = {t for t in _IDENT_RE.findall(text) if t not in STOPWORDS and len(t) > 1}
    return idents, literals


@dataclass
class ErrorSignature:
    """Weighted token set built from the error lines and the error message."""

    weights: dict[str, float]
    literals: set[str]

    @classmethod
    def build(cls, parsed: ParsedTrace, frames: list[Frame], bundle: ContextBundle) -> "ErrorSignature":
        weights: dict[str, float] = {}
        literals: set[str] = set()

        def add(text: str, w: float) -> None:
            ids, lits = extract_tokens(text)
            for t in ids:
                weights[t] = max(weights.get(t, 0), w)
            literals.update(lits)

        add(parsed.error.message, 2.0)
        for ctx in bundle.contexts:
            lines = ctx.snippet.splitlines()
            hl = ctx.highlight_line - ctx.start_line
            if 0 <= hl < len(lines):
                add(lines[hl], 2.0 if ctx.frame_index == bundle.contexts[0].frame_index else 1.5)
                for j in (hl - 2, hl - 1, hl + 1):
                    if 0 <= j < len(lines):
                        add(lines[j], 1.0)
        for f in frames[:5]:
            if f.function and f.function not in STOPWORDS:
                weights.setdefault(f.function, 1.0)
        # Quoted values in the message (e.g. '%Y-%m-%d', 'email') also count as literals
        literals.update(m.group("s") for m in _STRING_RE.finditer(parsed.error.message))
        return cls(weights=weights, literals=literals)

    def score_patch(self, patch: str) -> tuple[float, list[str]]:
        changed = []
        for ln in patch.splitlines():
            if ln.startswith(("+++", "---")):
                continue
            if ln.startswith(("+", "-")):
                changed.append(ln[1:])
            elif ln.startswith("@@"):
                changed.append(ln.split("@@")[-1])  # function name from the hunk header
        text = "\n".join(changed)
        ids, lits = extract_tokens(text)
        score, hits = 0.0, []
        for t in ids & set(self.weights):
            score += self.weights[t]
            hits.append(t)
        for lit in lits & self.literals:
            score += 3.0
            hits.append(repr(lit))
        return score, sorted(hits)


RELATED_THRESHOLD = 3.0


@dataclass
class SuspectResult:
    commit: Optional[SuspectCommit]
    recent: bool = False  # within the last N days
    blamed_error_line: bool = False
    warnings: list[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _newest_in_window(ranges: list[BlameRange], line: int, radius: int) -> Optional[BlameRange]:
    lo, hi = line - radius, line + radius
    hits = [r for r in ranges if r.start <= hi and r.end >= lo]
    if not hits:
        return None
    return max(hits, key=lambda r: _parse_date(r.commit.date) or datetime.min.replace(tzinfo=timezone.utc))


def _file_patch(detail: CommitDetail, path: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if path:
        for f in detail.files:
            if f.filename == path and f.patch:
                return f.filename, f.patch
    for f in detail.files:
        if f.patch:
            return f.filename, f.patch
    return None, None


def combined_patch(
    detail: CommitDetail,
    primary: Optional[str],
    exts: tuple[str, ...] = (),
    max_files: int = 6,
    max_lines: int = 250,
) -> Optional[str]:
    """Joins the patches of every file in the commit, each with its file header.

    The real change often lives in another file of the same commit rather than in the blamed
    file (e.g. the API module that renamed a field). The primary file comes first, then files
    in the same language.
    """
    files = [f for f in detail.files if f.patch]
    if not files:
        return None

    def rank(f) -> tuple[int, int]:
        return (0 if f.filename == primary else 1, 0 if exts and f.filename.endswith(exts) else 1)

    files.sort(key=rank)
    chunks = []
    for f in files[:max_files]:
        chunks.append(f"--- a/{f.filename}\n+++ b/{f.filename}\n{f.patch.rstrip()}")
    if len(files) > max_files:
        chunks.append(f"... ({len(files) - max_files} more changed files not shown)")
    return _trim_patch("\n".join(chunks), max_lines)


def _is_recent(date: str, days: int, now: Optional[datetime] = None) -> bool:
    d = _parse_date(date)
    if not d:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - d).total_seconds() <= days * 86400


def _to_suspect(
    info: CommitInfo, file: Optional[str], patch: Optional[str], selection: str, reason: str
) -> SuspectCommit:
    return SuspectCommit(
        sha=info.sha,
        message=info.message,
        author=info.author,
        date=info.date,
        url=info.url,
        patch=_trim_patch(patch, 250),
        file=file,
        selection=selection,  # type: ignore[arg-type]
        selection_reason=reason,
    )


def _trim_patch(patch: Optional[str], max_lines: int = 200) -> Optional[str]:
    if not patch:
        return patch
    lines = patch.splitlines()
    if len(lines) <= max_lines:
        return patch
    return "\n".join(lines[:max_lines] + [f"... ({len(lines) - max_lines} lines truncated)"])


def _short(sha: str) -> str:
    return sha[:7]


async def find_suspect(
    source: RepoSource,
    sha: str,
    parsed: ParsedTrace,
    frames: list[Frame],
    bundle: ContextBundle,
    language: Language,
    blame_radius: int = 3,
    recent_days: int = 14,
    related_limit: int = 12,
    now: Optional[datetime] = None,
) -> SuspectResult:
    """Finds the suspect commit.

    1. Blame the ±radius window around the error line of each user frame we have context
       for, and take the newest commit among the intersecting ranges.
    2. The error lines have not changed since that commit, so look through later commits for
       patches containing identifiers/literals related to the error (a "related commit").
       This catches cases where the line the trace points to is not the actual cause.
    """
    warnings: list[str] = []
    ctx_frames = [(c.file, c.highlight_line, c.frame_index) for c in bundle.contexts]
    if not ctx_frames:
        return SuspectResult(None, skipped_reason="No resolved user-code frame to blame.")

    blame_hits: list[tuple[int, BlameRange, str]] = []  # (frame order, range, file)
    unique_files = sorted({f for f, _, _ in ctx_frames})
    blames = await asyncio.gather(*(source.blame(sha, p) for p in unique_files), return_exceptions=True)
    blame_by_file: dict[str, list[BlameRange]] = {}
    for p, b in zip(unique_files, blames):
        if isinstance(b, BaseException):
            warnings.append(str(b))
        else:
            blame_by_file[p] = b
    for order, (file, line, _) in enumerate(ctx_frames):
        ranges = blame_by_file.get(file)
        if ranges:
            hit = _newest_in_window(ranges, line, blame_radius)
            if hit:
                blame_hits.append((order, hit, file))

    blame_pick: Optional[tuple[int, BlameRange, str]] = None
    if blame_hits:
        blame_pick = max(
            blame_hits,
            key=lambda h: (_parse_date(h[1].commit.date) or datetime.min.replace(tzinfo=timezone.utc), -h[0]),
        )

    # ------------------------------------------------------------- related commit search
    related_pick: Optional[tuple[float, CommitDetail, str, str, list[str]]] = None
    try:
        recent = await source.list_commits(sha, limit=related_limit + 5)
    except SourceError as exc:
        recent = []
        warnings.append(f"Could not list recent commits: {exc}")
    cutoff = _parse_date(blame_pick[1].commit.date) if blame_pick else None
    candidates = [
        c for c in recent if cutoff is None or ((_parse_date(c.date) or cutoff) > cutoff)
    ][:related_limit]
    if candidates:
        signature = ErrorSignature.build(parsed, frames, bundle)
        exts = LANG_EXTENSIONS.get(language, ())
        details = await asyncio.gather(*(source.get_commit(c.sha) for c in candidates), return_exceptions=True)
        stack_files = {f.file for f in frames if f.resolved}
        for d in details:
            if isinstance(d, BaseException) or d.parents > 1:
                continue
            best_file: Optional[tuple[float, str, str, list[str]]] = None
            for cf in d.files:
                if not cf.patch or not cf.filename.endswith(exts):
                    continue
                score, hits = signature.score_patch(cf.patch)
                if cf.filename in stack_files:
                    score += 1.0
                if best_file is None or score > best_file[0]:
                    best_file = (score, cf.filename, cf.patch, hits)
            if best_file and best_file[0] >= RELATED_THRESHOLD:
                if related_pick is None or best_file[0] > related_pick[0]:
                    related_pick = (best_file[0], d, best_file[1], best_file[2], best_file[3])

    if related_pick:
        score, detail, file, patch, hits = related_pick
        base = (
            f"The commit that last touched the error line ({_short(blame_pick[1].commit.sha)}) is older; "
            if blame_pick
            else ""
        )
        reason = (
            f"{base}a later commit, {_short(detail.info.sha)}, changed expressions related to the error "
            f"in {file}: {', '.join(hits[:6])}"
        )
        full = combined_patch(detail, file, LANG_EXTENSIONS.get(language, ())) or patch
        commit = _to_suspect(detail.info, file, full, "related", reason)
        return SuspectResult(
            commit, recent=_is_recent(detail.info.date, recent_days, now), blamed_error_line=False, warnings=warnings
        )

    if blame_pick:
        order, rng, file = blame_pick
        try:
            detail = await source.get_commit(rng.commit.sha)
            pfile, _ = _file_patch(detail, file)
            patch = combined_patch(detail, pfile or file, LANG_EXTENSIONS.get(language, ()))
        except SourceError as exc:
            warnings.append(f"Could not fetch the commit patch: {exc}")
            pfile, patch = file, None
        frame_desc = "the error line" if order == 0 else f"user frame #{order + 1}"
        line = ctx_frames[order][1]
        reason = f"This commit last changed the code around {file}:{line} ({frame_desc})"
        return SuspectResult(
            _to_suspect(rng.commit, pfile or file, patch, "blame", reason),
            recent=_is_recent(rng.commit.date, recent_days, now),
            blamed_error_line=order == 0,
            warnings=warnings,
        )

    return SuspectResult(
        None, warnings=warnings, skipped_reason="Could not blame" + (f": {warnings[0]}" if warnings else ".")
    )


def humanize_age(date: str, now: Optional[datetime] = None) -> str:
    d = _parse_date(date)
    if not d:
        return ""
    now = now or datetime.now(timezone.utc)
    secs = (now - d).total_seconds()
    if secs < 3600:
        return "just now"
    if secs < 86400:
        hours = int(secs // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(secs // 86400)
    if days < 60:
        return f"{days} day{'s' if days != 1 else ''} ago"
    return f"{days // 30} months ago"


def frame_label(f: Frame | RawFrame) -> str:
    name = PurePosixPath(f.file.replace("\\", "/")).name
    return f"{name}:{f.line}"
