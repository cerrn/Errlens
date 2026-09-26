"""History and result cache (SQLite, a single table)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import aiosqlite

from app.models import AnalysisResult, HistoryItem
from app.parser import normalize_trace

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    cache_key TEXT,
    created_at TEXT NOT NULL,
    repo TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    confidence REAL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_cache_key ON analyses(cache_key);
CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses(created_at);
"""


def make_cache_key(trace: str, repo: str, sha: str) -> str:
    h = hashlib.sha256()
    for part in (normalize_trace(trace), repo.lower(), sha):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Storage:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def save(self, result: AnalysisResult, cache_key: Optional[str]) -> None:
        # the "cached" flag and duration are not stored; they are recomputed on read
        stored = result.model_copy(update={"meta": result.meta.model_copy(update={"cached": False})})
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO analyses (id, cache_key, created_at, repo, error_type, error_message, confidence, result_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.id,
                    cache_key,
                    result.created_at,
                    result.meta.repo,
                    result.error.type,
                    result.error.message.splitlines()[0][:300] if result.error.message else "",
                    result.analysis.confidence if result.analysis else None,
                    stored.model_dump_json(),
                ),
            )
            await db.commit()

    async def get(self, analysis_id: str) -> Optional[AnalysisResult]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT result_json FROM analyses WHERE id = ?", (analysis_id,)) as cur:
                row = await cur.fetchone()
        return AnalysisResult.model_validate_json(row[0]) if row else None

    async def find_cached(self, cache_key: str) -> Optional[AnalysisResult]:
        """Only records with a completed analysis (confidence set) are used as cache hits."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT result_json FROM analyses WHERE cache_key = ? AND confidence IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            ) as cur:
                row = await cur.fetchone()
        return AnalysisResult.model_validate_json(row[0]) if row else None

    async def list(self, limit: int = 50) -> list[HistoryItem]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT id, created_at, repo, error_type, error_message, confidence FROM analyses "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            HistoryItem(id=r[0], created_at=r[1], repo=r[2], error_type=r[3], error_message=r[4], confidence=r[5])
            for r in rows
        ]

    async def delete_all(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM analyses")
            await db.commit()


def dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False)
