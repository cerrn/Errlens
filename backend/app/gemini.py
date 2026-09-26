"""Google Gemini provider (plain REST, no SDK).

Uses the same `report_analysis` tool through Gemini function calling. The tool call is
forced with `toolConfig.functionCallingConfig.mode = ANY`. When validation fails, the error
goes back to the model as a `functionResponse` (the equivalent of Anthropic's tool_result).

Hitting the per-minute limit is normal on the free tier: on 429 and 503 responses the
client waits for Gemini's suggested `retryDelay` and tries again.
"""
from __future__ import annotations

import asyncio
import copy
import re
from typing import Any, Optional

import httpx

from app.analyzer import TOOL_NAME, TOOL_SCHEMA, LLMError, LLMResponse

DEFAULT_API_URL = "https://generativelanguage.googleapis.com/v1beta"

# Keys supported by the Gemini Schema object (an OpenAPI subset)
_SUPPORTED_KEYS = {
    "type", "format", "description", "nullable", "enum", "properties", "required", "items",
    "minItems", "maxItems", "minimum", "maximum",
}


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Converts JSON Schema into Gemini's OpenAPI-based Schema format.

    - type: ["object", "null"]  ->  type: "OBJECT", nullable: true
    - Types are upper case (OBJECT, STRING, ...)
    - Nullable fields stay in `required`; otherwise Gemini tends to omit them entirely
    """
    s = copy.deepcopy(schema)
    out: dict[str, Any] = {}
    t = s.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        if "null" in t:
            out["nullable"] = True
        t = non_null[0] if non_null else "string"
    if t:
        out["type"] = str(t).upper()
    for key, val in s.items():
        if key in ("type",) or key not in _SUPPORTED_KEYS:
            continue
        if key == "properties":
            out["properties"] = {k: to_gemini_schema(v) for k, v in val.items()}
        elif key == "items":
            out["items"] = to_gemini_schema(val)
        else:
            out[key] = val
    return out


FUNCTION_DECLARATION = {
    "name": TOOL_NAME,
    "description": TOOL_SCHEMA["description"],
    "parameters": to_gemini_schema(TOOL_SCHEMA["input_schema"]),
}


def _parse_retry_delay(body: dict[str, Any]) -> Optional[float]:
    for d in (body.get("error") or {}).get("details", []) or []:
        delay = d.get("retryDelay")
        if isinstance(delay, str):
            m = re.match(r"^([\d.]+)s$", delay)
            if m:
                return float(m.group(1))
    return None


class GeminiLLM:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        max_output_tokens: int = 8192,
        timeout: float = 90.0,
        api_url: str = DEFAULT_API_URL,
        max_rate_limit_retries: int = 3,
        max_wait_seconds: float = 45.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.model = model
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.api_url = api_url.rstrip("/")
        self.max_rate_limit_retries = max_rate_limit_retries
        self.max_wait_seconds = max_wait_seconds
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self.last_usage: dict[str, Any] = {}

    # ------------------------------------------------------------------ message conversion

    @staticmethod
    def _to_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        contents = []
        for m in messages:
            if "_gemini" in m:  # raw Gemini content added by append_error
                contents.append(m["_gemini"])
            elif isinstance(m.get("content"), str):
                role = "model" if m.get("role") == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
            else:
                raise LLMError("Unsupported message format for Gemini.")
        return contents

    def append_error(self, messages: list[dict[str, Any]], resp: LLMResponse, feedback: str) -> list[dict[str, Any]]:
        """Sends a validation error back to the model as a functionResponse."""
        model_parts = resp.assistant_content or [{"text": resp.text or "(empty response)"}]
        out = list(messages)
        out.append({"_gemini": {"role": "model", "parts": model_parts}})
        if resp.tool_input is not None:
            out.append(
                {
                    "_gemini": {
                        "role": "user",
                        "parts": [{"functionResponse": {"name": TOOL_NAME, "response": {"error": feedback}}}],
                    }
                }
            )
        else:
            out.append({"_gemini": {"role": "user", "parts": [{"text": feedback}]}})
        return out

    # ------------------------------------------------------------------ call

    def _body(self, system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._to_contents(messages),
            "tools": [{"functionDeclarations": [FUNCTION_DECLARATION]}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [TOOL_NAME]}},
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": self.max_output_tokens},
        }

    async def call(self, system: str, messages: list[dict[str, Any]]) -> LLMResponse:
        url = f"{self.api_url}/models/{self.model}:generateContent"
        body = self._body(system, messages)
        attempt = 0
        while True:
            try:
                resp = await self._client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
            except httpx.TimeoutException as exc:
                raise LLMError("The Gemini response timed out.") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Could not connect to the Gemini API: {exc}") from exc

            if resp.status_code in (429, 500, 503) and attempt < self.max_rate_limit_retries:
                try:
                    delay = _parse_retry_delay(resp.json())
                except ValueError:
                    delay = None
                wait = min(self.max_wait_seconds, delay if delay is not None else 2.0 * (2**attempt))
                attempt += 1
                await asyncio.sleep(wait)
                continue
            break

        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
                msg = err.get("message", resp.text[:300])
                status = err.get("status", "")
            except ValueError:
                msg, status = resp.text[:300], ""
            if resp.status_code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
                raise LLMError(f"The Gemini API key is invalid or not authorized (GEMINI_API_KEY): {msg}")
            if resp.status_code == 429:
                raise LLMError(f"Gemini rate limit or free-tier quota reached, try again shortly: {msg[:200]}")
            if resp.status_code == 404:
                raise LLMError(f"Gemini model not found ({self.model}): {msg[:200]}")
            raise LLMError(f"Gemini API error ({resp.status_code}): {msg[:300]}")

        data = resp.json()
        self.last_usage = data.get("usageMetadata", {})
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "unknown")
            raise LLMError(f"Gemini returned no candidates (reason: {reason}).")
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        tool_input: Optional[dict[str, Any]] = None
        text = ""
        for p in parts:
            fc = p.get("functionCall")
            if fc and fc.get("name") == TOOL_NAME:
                tool_input = dict(fc.get("args") or {})
            elif "text" in p and not p.get("thought"):
                text += p["text"]
        if tool_input is None and cand.get("finishReason") == "MAX_TOKENS":
            raise LLMError("The Gemini response was cut off at the output token limit (maxOutputTokens).")
        # The raw parts (including thoughtSignature) are sent back verbatim on retry
        return LLMResponse(
            tool_input=tool_input,
            tool_use_id=TOOL_NAME if tool_input is not None else None,
            assistant_content=parts,
            text=text,
        )

    async def list_models(self) -> list[str]:
        resp = await self._client.get(
            f"{self.api_url}/models", params={"pageSize": "200"}, headers={"x-goog-api-key": self.api_key}
        )
        if resp.status_code >= 400:
            raise LLMError(f"Could not list models ({resp.status_code}): {resp.text[:200]}")
        return [
            m["name"].removeprefix("models/")
            for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
