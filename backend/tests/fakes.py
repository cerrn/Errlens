"""Fake LLM clients for tests."""
from __future__ import annotations

import re
from typing import Any, Optional

from app.analyzer import LLMResponse


class ScriptedLLM:
    """Returns pre-set tool inputs in order and records the messages it receives."""

    model = "fake-scripted"

    def __init__(self, outputs: list[Optional[dict[str, Any]]]):
        self.outputs = list(outputs)
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def call(self, system: str, messages: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append((system, [dict(m) for m in messages]))
        out = self.outputs.pop(0)
        tid = f"toolu_{len(self.calls)}"
        content = [{"type": "tool_use", "id": tid, "name": "report_analysis", "input": out or {}}]
        return LLMResponse(tool_input=out, tool_use_id=tid, assistant_content=content)


class DemoLLM:
    """Recognizes the demo scenarios and answers the way a real model would.

    It finds the target line in the numbered snippet of the prompt and computes line
    numbers itself, so it also checks that the pipeline builds the prompt correctly.
    """

    model = "fake-demo"

    def __init__(self) -> None:
        self.calls = 0
        self.last_user_message = ""

    @staticmethod
    def _find_line(msg: str, needle: str) -> int:
        for ln in msg.splitlines():
            m = re.match(r"^(?:>>|  ) +(\d+) \| (.*)$", ln)
            if m and needle in m.group(2):
                return int(m.group(1))
        raise AssertionError(f"Line not found in the prompt: {needle}")

    async def call(self, system: str, messages: list[dict[str, Any]]) -> LLMResponse:
        self.calls += 1
        msg = messages[0]["content"]
        self.last_user_message = msg
        if "KeyError: 'email'" in msg:
            line = self._find_line(msg, 'user_data["email"]')
            out = {
                "summary": "The profile page reads an 'email' field that no longer exists in the user data.",
                "root_cause": "The refactor commit renamed the API field to 'email_address'; "
                "get_profile still reads the old 'email' key.",
                "evidence": [
                    f"shop/profile.py:{line} reads user_data[\"email\"]",
                    "the refactor commit changed email to email_address in serialize_user",
                ],
                "confidence": 0.9,
                "fix": {
                    "file": "shop/profile.py",
                    "start_line": line,
                    "end_line": line,
                    "new_code": '        "email": mask_email(user_data["email_address"]),',
                    "explanation": "Read the field name used by the new schema.",
                },
            }
        elif "reading 'name'" in msg:
            line = self._find_line(msg, "return `Hello, ${user.name}!`")
            out = {
                "summary": "greeting reads the name property of the null value returned by findUser.",
                "root_cause": "findUser now returns null for inactive users; greeting does not handle that case.",
                "evidence": [f"scripts/greet.js:{line} reads user.name", "users.js added `return null`"],
                "confidence": 0.85,
                "fix": {
                    "file": "scripts/greet.js",
                    "start_line": line,
                    "end_line": line,
                    "new_code": "  if (!user) return `User ${id} not found or inactive.`;\n"
                    "  return `Hello, ${user.name}!`;",
                    "explanation": "Handle the null return value.",
                },
            }
        elif "unconverted data remains" in msg:
            line = self._find_line(msg, "datetime.strptime(value")
            out = {
                "summary": "The report parses an ISO 8601 date with the old '%Y-%m-%d' format.",
                "root_cause": "The ISO 8601 commit switched format_order_date to isoformat(); "
                "parse_order_date still expects the old format.",
                "evidence": [f"shop/reports.py:{line} uses strptime '%Y-%m-%d'", "dates.py switched to isoformat"],
                "confidence": 0.88,
                "fix": {
                    "file": "shop/reports.py",
                    "start_line": line,
                    "end_line": line,
                    "new_code": "return datetime.fromisoformat(value).date()",  # unindented: exercises reindent
                    "explanation": "Parse the ISO 8601 string with fromisoformat.",
                },
            }
        else:
            out = {
                "summary": "Unknown error",
                "root_cause": "Not enough evidence",
                "evidence": [],
                "confidence": 0.2,
                "fix": None,
            }
        tid = f"toolu_demo_{self.calls}"
        return LLMResponse(
            tool_input=out,
            tool_use_id=tid,
            assistant_content=[{"type": "tool_use", "id": tid, "name": "report_analysis", "input": out}],
        )
