import json

import httpx
import pytest

from app.main import create_app
from app.pipeline import PipelineDeps
from app.storage import Storage

from .fakes import DemoLLM
from .test_pipeline import settings


@pytest.fixture
async def client(tmp_path):
    s = settings(tmp_path, cors_origins="http://localhost:3000")
    llm = DemoLLM()
    deps = PipelineDeps(settings=s, storage=Storage(s.errlens_db_path), llm_factory=lambda: llm)
    app = create_app(s, deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.llm = llm  # type: ignore[attr-defined]
            yield c


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [ln[5:].lstrip() for ln in block.split("\n") if ln.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_analyze_and_history(client, demo_repo, traces_dir):
    body = {"trace": (traces_dir / "demo_bug1_keyerror.txt").read_text(), "repo_url": str(demo_repo), "ref": "main"}
    r = await client.post("/analyze", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["id"].startswith("an_") and res["fix"]["file"] == "shop/profile.py"

    h = (await client.get("/history")).json()["items"]
    assert h[0]["id"] == res["id"] and h[0]["error_type"] == "KeyError"
    one = await client.get(f"/history/{res['id']}")
    assert one.status_code == 200 and one.json()["fix"]["diff"] == res["fix"]["diff"]
    assert (await client.get("/history/an_missing")).status_code == 404

    # The same request is served from cache the second time
    r2 = (await client.post("/analyze", json=body)).json()
    assert r2["meta"]["cached"] is True and client.llm.calls == 1


async def test_analyze_errors(client, demo_repo):
    r = await client.post("/analyze", json={"trace": "   ", "repo_url": "x/y"})
    assert r.status_code == 422
    r = await client.post("/analyze", json={"trace": "garbage", "repo_url": str(demo_repo)})
    assert r.status_code == 422 and r.json()["code"] == "parse_error"
    tb = 'Traceback (most recent call last):\n  File "a.py", line 1, in f\nKeyError: 1\n'
    r = await client.post("/analyze", json={"trace": tb, "repo_url": "https://gitlab.com/a/b"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_repo_url"


async def test_stream_events_arrive_in_order(client, demo_repo, traces_dir):
    body = {"trace": (traces_dir / "demo_bug3_date_format.txt").read_text(), "repo_url": str(demo_repo)}
    async with client.stream("POST", "/analyze/stream", json=body) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-accel-buffering"] == "no"
        text = "".join([chunk async for chunk in r.aiter_text()])
    events = parse_sse(text)
    order = [e["step"] for e in events if e["status"] != "running"]
    assert order == ["parse", "resolve_paths", "fetch_context", "blame", "analyze", "build_fix", "done"]
    final = events[-1]["data"]["result"]
    assert final["suspect_commit"]["file"] == "shop/dates.py"
    assert final["fix"]["diff"].startswith("--- a/shop/reports.py")


async def test_cors_preflight(client):
    r = await client.options(
        "/analyze/stream",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
