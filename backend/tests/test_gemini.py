"""GeminiLLM: request format, forced function calling, retry with functionResponse, 429 backoff."""
import json

import httpx
import pytest

from app.analyzer import LLMError, run_analysis
from app.config import Settings
from app.gemini import FUNCTION_DECLARATION, GeminiLLM, to_gemini_schema
from app.main import default_llm_factory
from app.models import CodeContext

CTX = [CodeContext(file="a.py", start_line=1, end_line=3, highlight_line=2, snippet="x = 1\ny = d['k']\nz = 3")]
FILES = {"a.py": "x = 1\ny = d['k']\nz = 3\n"}


def fc_response(args, signature="sig-1"):
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "thinking", "thought": True},
                        {"functionCall": {"name": "report_analysis", "args": args}, "thoughtSignature": signature},
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
    }


def base(fix):
    return {"summary": "s", "root_cause": "r", "evidence": ["a.py:2"], "confidence": 0.8, "fix": fix}


def make(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiLLM("key-123", "gemini-test", http_client=client, max_wait_seconds=0.01, **kw)


def test_schema_conversion():
    params = FUNCTION_DECLARATION["parameters"]
    assert params["type"] == "OBJECT"
    fix = params["properties"]["fix"]
    assert fix["type"] == "OBJECT" and fix["nullable"] is True
    assert "fix" in params["required"]  # may be null, but the model must write the field
    assert params["properties"]["evidence"]["items"]["type"] == "STRING"
    assert params["properties"]["confidence"]["maximum"] == 1
    assert to_gemini_schema({"type": ["string", "null"], "title": "x"}) == {"type": "STRING", "nullable": True}


async def test_request_format_and_retry_with_function_response():
    reqs = []
    good = base({"file": "a.py", "start_line": 2, "end_line": 2, "new_code": "y = d.get('k')", "explanation": "e"})
    bad = base({"file": "zzz.py", "start_line": 2, "end_line": 2, "new_code": "y = 1", "explanation": "e"})
    responses = [fc_response(bad, "sig-A"), fc_response(good, "sig-B")]

    def handler(req: httpx.Request):
        reqs.append(req)
        return httpx.Response(200, json=responses[len(reqs) - 1])

    llm = make(handler)
    res = await run_analysis(llm, "user message", CTX, FILES)
    assert res.attempts == 2 and res.built_fix is not None

    assert str(reqs[0].url).endswith("/models/gemini-test:generateContent")
    assert reqs[0].headers["x-goog-api-key"] == "key-123"
    b1 = json.loads(reqs[0].content)
    assert b1["toolConfig"]["functionCallingConfig"] == {"mode": "ANY", "allowedFunctionNames": ["report_analysis"]}
    assert b1["systemInstruction"]["parts"][0]["text"].startswith("You are an experienced")
    assert b1["contents"] == [{"role": "user", "parts": [{"text": "user message"}]}]

    b2 = json.loads(reqs[1].content)
    assert [c["role"] for c in b2["contents"]] == ["user", "model", "user"]
    # model parts, including thoughtSignature, are sent back verbatim
    assert b2["contents"][1]["parts"][1]["thoughtSignature"] == "sig-A"
    fr = b2["contents"][2]["parts"][0]["functionResponse"]
    assert fr["name"] == "report_analysis" and "is not one of the files shown" in fr["response"]["error"]


async def test_rate_limit_waits_then_succeeds():
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                                        "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}]}})
        return httpx.Response(200, json=fc_response(base(None)))

    res = await make(handler).call("sys", [{"role": "user", "content": "m"}])
    assert len(calls) == 2 and res.tool_input["summary"] == "s"


@pytest.mark.parametrize(
    "status,body,needle",
    [
        (400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid"}}, "API error"),
        (403, {"error": {"status": "PERMISSION_DENIED", "message": "denied"}}, "invalid or not authorized"),
        (404, {"error": {"status": "NOT_FOUND", "message": "no model"}}, "model not found"),
        (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}, "quota reached"),
    ],
)
async def test_error_messages(status, body, needle):
    llm = make(lambda req: httpx.Response(status, json=body), max_rate_limit_retries=0)
    with pytest.raises(LLMError, match=needle):
        await llm.call("sys", [{"role": "user", "content": "m"}])


async def test_blocked_prompt():
    llm = make(lambda req: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LLMError, match="SAFETY"):
        await llm.call("sys", [{"role": "user", "content": "m"}])


def test_provider_selection():
    s = Settings(_env_file=None, anthropic_api_key="", gemini_api_key="g")
    assert s.resolved_llm_provider == "gemini"
    assert isinstance(default_llm_factory(s)(), GeminiLLM)
    s2 = Settings(_env_file=None, anthropic_api_key="a", gemini_api_key="g")
    assert s2.resolved_llm_provider == "anthropic"
    s3 = Settings(_env_file=None, anthropic_api_key="a", gemini_api_key="g", llm_provider="gemini")
    assert s3.resolved_llm_provider == "gemini"
    assert Settings(_env_file=None, anthropic_api_key="", gemini_api_key="").resolved_llm_provider is None
