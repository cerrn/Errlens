"""Checks that AnthropicLLM builds requests with the real SDK and parses tool_use responses (mocked HTTP)."""
import json

try:  # anthropic>=1.x uses httpx2, older versions use httpx
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from app.analyzer import TOOL_NAME, AnthropicLLM, run_analysis
from app.models import CodeContext


def tool_response(inp, tid="toolu_01"):
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [{"type": "tool_use", "id": tid, "name": TOOL_NAME, "input": inp}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


async def test_anthropic_request_and_retry_flow():
    ctx = [CodeContext(file="a.py", start_line=1, end_line=3, highlight_line=2, snippet="x = 1\ny = d['k']\nz = 3")]
    files = {"a.py": "x = 1\ny = d['k']\nz = 3\n"}
    bad = {"summary": "s", "root_cause": "r", "evidence": [], "confidence": 0.7,
           "fix": {"file": "b.py", "start_line": 2, "end_line": 2, "new_code": "y = 1", "explanation": "e"}}
    good = dict(bad, fix={"file": "a.py", "start_line": 2, "end_line": 2, "new_code": "y = d.get('k')", "explanation": "e"})
    responses = [tool_response(bad, "toolu_1"), tool_response(good, "toolu_2")]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses[len(requests) - 1])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = AnthropicLLM(api_key="sk-test", model="claude-sonnet-5", base_url="https://api.anthropic.com", http_client=client)
    res = await run_analysis(llm, "user message", ctx, files)
    assert res.attempts == 2 and res.built_fix is not None
    assert str(requests[0].url) == "https://api.anthropic.com/v1/messages"
    assert requests[0].headers["x-api-key"] == "sk-test"

    first = json.loads(requests[0].content)
    assert first["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert first["tools"][0]["name"] == TOOL_NAME and first["model"] == "claude-sonnet-5"
    second = json.loads(requests[1].content)
    assert second["messages"][1]["content"][0]["type"] == "tool_use"
    tr = second["messages"][2]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "toolu_1" and tr["is_error"] is True
