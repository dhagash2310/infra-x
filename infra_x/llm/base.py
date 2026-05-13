"""LLM provider interface — kept tiny on purpose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResponse:
    """Result of a structured LLM call."""

    content: str
    parsed: Any | None = None  # populated when JSON-mode is requested
    raw: dict[str, Any] | None = None


class LLMProvider(Protocol):
    """Anything that can answer a prompt."""

    name: str
    model: str

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Single-turn completion. `json_mode=True` requests structured JSON output."""
        ...
