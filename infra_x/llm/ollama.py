"""
Ollama provider.

Talks to a local `ollama serve` over its HTTP API. We use `/api/chat` (not the
older `/api/generate`) so we can pass a system prompt cleanly and so we can
toggle JSON mode via `format: "json"`.

Defaults are tuned for an 8–16GB-RAM dev machine running `qwen2.5-coder:7b`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from infra_x.llm.base import LLMResponse

DEFAULT_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("INFRA_X_MODEL", "qwen2.5-coder:7b")


@dataclass
class OllamaProvider:
    """Local Ollama LLM provider."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout_s: float = 120.0
    name: str = "ollama"

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"

        url = f"{self.base_url.rstrip('/')}/api/chat"
        try:
            r = httpx.post(url, json=payload, timeout=self.timeout_s)
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Could not reach Ollama at {self.base_url}. "
                "Is `ollama serve` running? "
                "Try `brew install ollama && ollama serve &` and "
                f"`ollama pull {self.model}`."
            ) from e
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise RuntimeError(
                f"Ollama returned {e.response.status_code}: {body}"
            ) from e

        data = r.json()
        content = data.get("message", {}).get("content", "")
        parsed: Any | None = None
        if json_mode:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # Some smaller models prepend prose to the JSON. Best-effort recovery.
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
