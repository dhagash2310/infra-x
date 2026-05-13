"""
Anthropic provider.

Talks to api.anthropic.com via the Messages API. Auth is via the
ANTHROPIC_API_KEY env var (or pass `api_key=...` directly).

JSON mode note
--------------
Anthropic doesn't expose a `format: json` flag like Ollama / OpenAI. The
canonical pattern is "prefill the assistant turn with `{`" — the model is
strongly biased to continue valid JSON from there. We do that automatically
when `json_mode=True`, then re-prepend the `{` before parsing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from infra_x.llm.base import LLMResponse

DEFAULT_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
DEFAULT_MODEL = os.environ.get("INFRA_X_ANTHROPIC_MODEL", "claude-sonnet-4-6")
DEFAULT_API_VERSION = "2023-06-01"


@dataclass
class AnthropicProvider:
    """Anthropic Messages API client."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    timeout_s: float = 120.0
    max_tokens: int = 4096
    name: str = "anthropic"

    def __post_init__(self) -> None:
        # Resolve the key lazily so unit tests can construct the provider
        # without an env var; we only require the key when complete() is called.
        if self.api_key is None:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")

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
                "ANTHROPIC_API_KEY is not set. Export it or pass --provider ollama "
                "to use a local model. Get a key at https://console.anthropic.com."
            )

        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        if json_mode:
            # Prefill the assistant turn with `{` to bias toward valid JSON.
            messages.append({"role": "assistant", "content": "{"})

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "temperature": temperature,
        }

        url = f"{self.base_url.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": DEFAULT_API_VERSION,
            "content-type": "application/json",
        }

        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=self.timeout_s)
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Could not reach Anthropic at {self.base_url}. "
                "Check your network connection."
            ) from e
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise RuntimeError(
                f"Anthropic returned {e.response.status_code}: {body}"
            ) from e

        data = r.json()
        # The Messages API returns content as a list of blocks; we want the text.
        blocks = data.get("content", [])
        text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        content = "".join(text_parts)

        # If we prefilled with `{`, that character is NOT echoed in the response,
        # so we re-prepend it before parsing.
        parsed: Any | None = None
        if json_mode:
            full = "{" + content
            try:
                parsed = json.loads(full)
            except json.JSONDecodeError:
                parsed = _extract_first_json_object(full)
            content = full  # so callers see what we actually parsed

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
