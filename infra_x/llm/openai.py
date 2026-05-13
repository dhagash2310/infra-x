"""
OpenAI provider.

Talks to api.openai.com via the Chat Completions API. Auth is via the
OPENAI_API_KEY env var (or pass `api_key=...` directly).

JSON mode uses the native `response_format={"type": "json_object"}` flag,
which forces the model to emit valid JSON.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from infra_x.llm.base import LLMResponse

DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
DEFAULT_MODEL = os.environ.get("INFRA_X_OPENAI_MODEL", "gpt-4o-mini")


@dataclass
class OpenAIProvider:
    """OpenAI Chat Completions client."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    timeout_s: float = 120.0
    name: str = "openai"

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("OPENAI_API_KEY")

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it or pass --provider ollama "
                "to use a local model. Get a key at https://platform.openai.com/api-keys."
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=self.timeout_s)
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Could not reach OpenAI at {self.base_url}. "
                "Check your network connection."
            ) from e
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise RuntimeError(
                f"OpenAI returned {e.response.status_code}: {body}"
            ) from e

        data = r.json()
        choices = data.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""

        parsed: Any | None = None
        if json_mode and content:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = _extract_first_json_object(content)

        return LLMResponse(content=content, parsed=parsed, raw=data)


def _extract_first_json_object(s: str) -> Any | None:
    """Find the first balanced `{...}` block in `s` and parse it."""
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(s[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None
