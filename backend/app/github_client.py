"""Code source clients.

`GitHubSource` talks to the GitHub REST + GraphQL APIs. `LocalGitSource` implements the
same interface on top of a local git repository; it is used for offline tests and
rehearsals (with ERRLENS_ALLOW_LOCAL_REPOS=true a local path can be passed as repo_url).

Responses keyed by a commit SHA never change, so they are kept in an in-process cache.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol
from urllib.parse import quote

import httpx

# --------------------------------------------------------------------------- errors


class SourceError(Exception):
    """Error while accessing the code source. The message is safe to show to users."""

    code = "source_error"


class RepoNotFound(SourceError):
    code = "repo_not_found"


class RefNotFound(SourceError):
    code = "ref_not_found"


class FileNotInRepo(SourceError):
    code = "file_not_found"


class RateLimited(SourceError):
    code = "rate_limited"


class BlameUnavailable(SourceError):
    code = "blame_unavailable"


class InvalidRepoUrl(SourceError):
    code = "invalid_repo_url"


# --------------------------------------------------------------------------- data types


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    date: str  # ISO 8601
    url: str


@dataclass
class CommitFile:
    filename: str
    status: str
    patch: Optional[str]


@dataclass
class CommitDetail:
    info: CommitInfo
    files: list[CommitFile] = field(default_factory=list)
    parents: int = 1


@dataclass
class BlameRange:
    start: int
    end: int
    commit: CommitInfo


@dataclass
class RepoRef:
    owner: str
    name: str
    ref: Optional[str] = None  # ref taken from the URL (/tree/<ref>)
    local_path: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class RepoSource(Protocol):
    repo: RepoRef

    async def resolve_ref(self, ref: Optional[str]) -> tuple[str, str]: ...
    async def get_tree(self, sha: str) -> tuple[list[str], bool]: ...
    async def get_file(self, sha: str, path: str) -> str: ...
    async def blame(self, sha: str, path: str) -> list[BlameRange]: ...
    async def get_commit(self, sha: str) -> CommitDetail: ...
    async def list_commits(self, sha: str, limit: int = 20) -> list[CommitInfo]: ...
    async def aclose(self) -> None: ...


# --------------------------------------------------------------------------- repo URL


_GH_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com[/:](?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?"
    r"(?:/(?:tree|blob|commit)/(?P<ref>[^?#]+?))?/?(?:[?#].*)?$"
)
_GH_SSH_RE = re.compile(r"^git@github\.com:(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?$")
_SHORT_RE = re.compile(r"^(?P<owner>[\w.-]+)/(?P<name>[\w.-]+)$")


def parse_repo_url(url: str, allow_local: bool = False) -> RepoRef:
    url = url.strip()
    if allow_local:
        local = url[len("file://") :] if url.startswith("file://") else url
        if (url.startswith("file://") or os.path.isabs(local)) and Path(local, ".git").exists():
            p = Path(local).resolve()
            return RepoRef(owner="local", name=p.name, local_path=str(p))
    for rx in (_GH_URL_RE, _GH_SSH_RE, _SHORT_RE):
        m = rx.match(url)
        if m:
            ref = m.groupdict().get("ref")
            return RepoRef(owner=m.group("owner"), name=m.group("name"), ref=ref)
    raise InvalidRepoUrl(
        "Invalid GitHub repository URL. Example: https://github.com/owner/repo or owner/repo"
    )


# --------------------------------------------------------------------------- cache


class _LRU:
    def __init__(self, maxsize: int = 2048):
        self.maxsize = maxsize
        self._d: OrderedDict[Any, Any] = OrderedDict()

    def get(self, key: Any) -> Any:
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def set(self, key: Any, value: Any) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()


# Immutable, SHA-keyed responses (tree, file, blame, commit), process-wide
IMMUTABLE_CACHE = _LRU(4096)
# Ref -> SHA resolution can change, so it is short-lived
_REF_CACHE: dict[tuple[str, str], tuple[float, tuple[str, str]]] = {}
REF_TTL_SECONDS = 60.0


def _ref_cache_get(key: tuple[str, str]) -> Optional[tuple[str, str]]:
    hit = _REF_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < REF_TTL_SECONDS:
        return hit[1]
    return None


# --------------------------------------------------------------------------- GitHub


BLAME_QUERY = """
query($owner:String!, $name:String!, $sha:GitObjectID!, $path:String!) {
  repository(owner:$owner, name:$name) {
    object(oid:$sha) {
      ... on Commit {
        blame(path:$path) {
          ranges { startingLine endingLine
            commit { oid message committedDate url author { name } } }
        }
      }
    }
  }
}
"""


class GitHubSource:
    def __init__(
        self,
        repo: RepoRef,
        token: str = "",
        api_url: str = "https://api.github.com",
        timeout: float = 15.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.repo = repo
        self.token = token
        self.api_url = api_url.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ErrLens",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.AsyncClient(headers=headers, timeout=timeout)
        self._owns_client = client is None
        if client is not None:
            self._client.headers.update(headers)

    @property
    def _repo_path(self) -> str:
        return f"/repos/{self.repo.owner}/{self.repo.name}"

    @property
    def graphql_url(self) -> str:
        if self.api_url.endswith("/api/v3"):
            return self.api_url[: -len("/v3")] + "/graphql"
        return self.api_url + "/graphql"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _raise_for(self, resp: httpx.Response, what: str) -> None:
        if resp.status_code < 400:
            return
        if resp.status_code in (403, 429) and (
            resp.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in resp.text.lower()
        ):
            reset = resp.headers.get("x-ratelimit-reset")
            when = ""
            if reset and reset.isdigit():
                mins = max(0, int((int(reset) - time.time()) / 60))
                when = f" It resets in about {mins} minutes."
            hint = "" if self.token else " Setting GITHUB_TOKEN raises the limit to 5000 requests per hour."
            raise RateLimited(f"GitHub API rate limit exceeded.{when}{hint}")
        if resp.status_code in (401,):
            raise SourceError("The GitHub token is invalid or expired (401).")
        if resp.status_code == 404:
            if what == "repo":
                raise RepoNotFound(
                    f"Repository '{self.repo.full_name}' not found. For a private repository the token needs access to it."
                )
            raise FileNotInRepo(f"{what} not found.")
        if resp.status_code == 422 and what == "ref":
            raise RefNotFound("The given branch/commit was not found.")
        raise SourceError(f"GitHub API error ({resp.status_code}) - {what}: {resp.text[:200]}")

    async def _get(self, path: str, what: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._client.get(self.api_url + path, **kwargs)
        except httpx.TimeoutException as exc:
            raise SourceError(f"Request to GitHub timed out ({what}).") from exc
        except httpx.HTTPError as exc:
            raise SourceError(f"Could not connect to GitHub: {exc}") from exc
        self._raise_for(resp, what)
        return resp

    async def resolve_ref(self, ref: Optional[str]) -> tuple[str, str]:
        key = (self.repo.full_name, ref or "")
        cached = _ref_cache_get(key)
        if cached:
            return cached
        if not ref:
            info = (await self._get(self._repo_path, "repo")).json()
            ref = info.get("default_branch") or "main"
        try:
            data = (await self._get(f"{self._repo_path}/commits/{quote(ref, safe='')}", "ref")).json()
        except FileNotInRepo as exc:
            # 404: either the repository or the ref does not exist
            await self._get(self._repo_path, "repo")
            raise RefNotFound(f"Branch/tag/commit '{ref}' not found.") from exc
        result = (ref, data["sha"])
        _REF_CACHE[key] = (time.monotonic(), result)
        return result

    async def get_tree(self, sha: str) -> tuple[list[str], bool]:
        key = ("tree", self.repo.full_name, sha)
        hit = IMMUTABLE_CACHE.get(key)
        if hit is not None:
            return hit
        data = (await self._get(f"{self._repo_path}/git/trees/{sha}", "tree", params={"recursive": "1"})).json()
        paths = [e["path"] for e in data.get("tree", []) if e.get("type") == "blob"]
        result = (paths, bool(data.get("truncated")))
        IMMUTABLE_CACHE.set(key, result)
        return result

    async def get_file(self, sha: str, path: str) -> str:
        key = ("file", self.repo.full_name, sha, path)
        hit = IMMUTABLE_CACHE.get(key)
        if hit is not None:
            return hit
        resp = await self._get(
            f"{self._repo_path}/contents/{quote(path)}",
            f"File '{path}'",
            params={"ref": sha},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        text = resp.content.decode("utf-8", errors="replace")
        IMMUTABLE_CACHE.set(key, text)
        return text

    async def blame(self, sha: str, path: str) -> list[BlameRange]:
        key = ("blame", self.repo.full_name, sha, path)
        hit = IMMUTABLE_CACHE.get(key)
        if hit is not None:
            return hit
        if not self.token:
            raise BlameUnavailable("Blame uses the GitHub GraphQL API, which requires a token (GITHUB_TOKEN).")
        try:
            resp = await self._client.post(
                self.graphql_url,
                json={
                    "query": BLAME_QUERY,
                    "variables": {"owner": self.repo.owner, "name": self.repo.name, "sha": sha, "path": path},
                },
            )
        except httpx.HTTPError as exc:
            raise BlameUnavailable(f"Could not fetch blame: {exc}") from exc
        self._raise_for(resp, "blame")
        body = resp.json()
        if body.get("errors"):
            raise BlameUnavailable("Could not fetch blame: " + "; ".join(e.get("message", "") for e in body["errors"]))
        obj = ((body.get("data") or {}).get("repository") or {}).get("object") or {}
        blame = obj.get("blame")
        if not blame:
            raise BlameUnavailable(f"No blame information for '{path}'.")
        ranges = []
        for r in blame.get("ranges", []):
            c = r["commit"]
            ranges.append(
                BlameRange(
                    start=r["startingLine"],
                    end=r["endingLine"],
                    commit=CommitInfo(
                        sha=c["oid"],
                        message=c.get("message", ""),
                        author=(c.get("author") or {}).get("name") or "",
                        date=c.get("committedDate", ""),
                        url=c.get("url", ""),
                    ),
                )
            )
        IMMUTABLE_CACHE.set(key, ranges)
        return ranges

    async def get_commit(self, sha: str) -> CommitDetail:
        key = ("commit", self.repo.full_name, sha)
        hit = IMMUTABLE_CACHE.get(key)
        if hit is not None:
            return hit
        data = (await self._get(f"{self._repo_path}/commits/{sha}", "commit")).json()
        detail = CommitDetail(
            info=_commit_info_from_rest(data),
            files=[CommitFile(f["filename"], f.get("status", ""), f.get("patch")) for f in data.get("files", [])],
            parents=len(data.get("parents", [])),
        )
        IMMUTABLE_CACHE.set(key, detail)
        return detail

    async def list_commits(self, sha: str, limit: int = 20) -> list[CommitInfo]:
        key = ("commits", self.repo.full_name, sha, limit)
        hit = IMMUTABLE_CACHE.get(key)
        if hit is not None:
            return hit
        data = (
            await self._get(f"{self._repo_path}/commits", "commits", params={"sha": sha, "per_page": str(limit)})
        ).json()
        result = [_commit_info_from_rest(c) for c in data]
        IMMUTABLE_CACHE.set(key, result)
        return result


def _commit_info_from_rest(data: dict[str, Any]) -> CommitInfo:
    commit = data.get("commit", {})
    author = commit.get("author") or {}
    committer = commit.get("committer") or {}
    return CommitInfo(
        sha=data["sha"],
        message=commit.get("message", ""),
        author=author.get("name") or (data.get("author") or {}).get("login") or "",
        date=committer.get("date") or author.get("date") or "",
        url=data.get("html_url", ""),
    )


# --------------------------------------------------------------------------- local git


class LocalGitSource:
    """Exposes a local git repository through the same interface as the GitHub client."""

    def __init__(self, repo: RepoRef):
        assert repo.local_path
        self.repo = repo
        self.path = repo.local_path
        self._web_base: Optional[str] = None

    async def aclose(self) -> None:
        return None

    async def _git(self, *args: str) -> str:
        # A thread instead of an asyncio subprocess: on Windows, uvicorn --reload uses a
        # SelectorEventLoop, which does not support asyncio.create_subprocess_exec.
        def run() -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=self.path, capture_output=True)

        proc = await asyncio.to_thread(run)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise SourceError(f"git {' '.join(args[:2])} failed: {err}")
        return proc.stdout.decode("utf-8", errors="replace")

    async def _url(self, sha: str) -> str:
        if self._web_base is None:
            try:
                remote = (await self._git("remote", "get-url", "origin")).strip()
                ref = parse_repo_url(remote)
                self._web_base = f"https://github.com/{ref.full_name}"
            except SourceError:
                self._web_base = ""
        return f"{self._web_base}/commit/{sha}" if self._web_base else ""

    async def resolve_ref(self, ref: Optional[str]) -> tuple[str, str]:
        name = ref
        if not name:
            try:
                name = (await self._git("symbolic-ref", "--short", "HEAD")).strip()
            except SourceError:
                name = "HEAD"
        try:
            sha = (await self._git("rev-parse", "--verify", f"{name}^{{commit}}")).strip()
        except SourceError as exc:
            raise RefNotFound(f"Branch/tag/commit '{name}' not found.") from exc
        return name, sha

    async def get_tree(self, sha: str) -> tuple[list[str], bool]:
        out = await self._git("ls-tree", "-r", "--name-only", sha)
        return [ln for ln in out.splitlines() if ln], False

    async def get_file(self, sha: str, path: str) -> str:
        try:
            return await self._git("show", f"{sha}:{path}")
        except SourceError as exc:
            raise FileNotInRepo(f"File '{path}' not found.") from exc

    async def blame(self, sha: str, path: str) -> list[BlameRange]:
        out = await self._git("blame", "--porcelain", sha, "--", path)
        commits: dict[str, dict[str, str]] = {}
        line_commit: list[tuple[int, str]] = []
        current: Optional[str] = None
        header_re = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")
        for ln in out.splitlines():
            m = header_re.match(ln)
            if m:
                current = m.group(1)
                commits.setdefault(current, {})
                line_commit.append((int(m.group(3)), current))
                continue
            if current is None or ln.startswith("\t"):
                continue
            key, _, val = ln.partition(" ")
            commits[current].setdefault(key, val)
        ranges: list[BlameRange] = []
        infos: dict[str, CommitInfo] = {}
        for sha_, meta in commits.items():
            ts = int(meta.get("committer-time", "0"))
            tz = meta.get("committer-tz", "+0000")
            infos[sha_] = CommitInfo(
                sha=sha_,
                message=meta.get("summary", ""),
                author=meta.get("author", ""),
                date=_iso_from_epoch(ts, tz),
                url=await self._url(sha_),
            )
        for lineno, sha_ in sorted(line_commit):
            if ranges and ranges[-1].commit.sha == sha_ and ranges[-1].end == lineno - 1:
                ranges[-1].end = lineno
            else:
                ranges.append(BlameRange(lineno, lineno, infos[sha_]))
        return ranges

    async def get_commit(self, sha: str) -> CommitDetail:
        info = (await self.list_commits(sha, 1))[0]
        parents = (await self._git("rev-list", "--parents", "-n", "1", sha)).split()[1:]
        base = parents[0] if parents else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # empty tree
        diff = await self._git("diff", "--no-color", "--no-renames", base, sha)
        files: list[CommitFile] = []
        for section in re.split(r"(?m)^diff --git ", diff)[1:]:
            header, _, rest = section.partition("\n@@")
            m = re.match(r"a/(.+?) b/(.+)", header.splitlines()[0])
            filename = m.group(2) if m else header.splitlines()[0]
            status = "added" if "new file mode" in header else "removed" if "deleted file mode" in header else "modified"
            patch = ("@@" + rest).rstrip("\n") if rest else None
            files.append(CommitFile(filename, status, patch))
        return CommitDetail(info=info, files=files, parents=len(parents))

    async def list_commits(self, sha: str, limit: int = 20) -> list[CommitInfo]:
        out = await self._git("log", f"-n{limit}", "--format=%H%x1f%an%x1f%cI%x1f%B%x1e", sha)
        result = []
        for rec in out.split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            h, author, date, msg = rec.split("\x1f", 3)
            result.append(CommitInfo(sha=h, message=msg.strip(), author=author, date=date, url=await self._url(h)))
        return result


def _iso_from_epoch(ts: int, tz: str) -> str:
    from datetime import datetime, timedelta, timezone

    sign = -1 if tz.startswith("-") else 1
    offset = timedelta(hours=int(tz[1:3] or 0), minutes=int(tz[3:5] or 0)) * sign
    return datetime.fromtimestamp(ts, tz=timezone(offset)).isoformat()


# --------------------------------------------------------------------------- factory


def make_source(repo_url: str, settings: Any) -> RepoSource:
    repo = parse_repo_url(repo_url, allow_local=settings.errlens_allow_local_repos)
    if repo.local_path:
        return LocalGitSource(repo)
    return GitHubSource(
        repo, token=settings.github_token, api_url=settings.github_api_url, timeout=settings.github_timeout
    )
