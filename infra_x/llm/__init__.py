"""LLM provider abstraction. Default: Ollama (local). BYO key for hosted providers."""

from infra_x.llm.anthropic import AnthropicProvider
from infra_x.llm.base import LLMProvider, LLMResponse
from infra_x.llm.ollama import OllamaProvider
from infra_x.llm.openai import OpenAIProvider

__all__ = [
    "AnthropicProvider",
    "LLMProvider",
    "LLMResponse",
    "OllamaProvider",
    "OpenAIProvider",
    "get_provider",
]


def get_provider(name: str = "ollama", **kwargs) -> LLMProvider:
    """Factory: return a provider by name.

    Supported names: "ollama" (default, local), "anthropic", "openai".

    Hosted providers read their API key from the corresponding env var
    (ANTHROPIC_API_KEY / OPENAI_API_KEY) unless `api_key=...` is passed.
    """
    name = name.lower()
    if name == "ollama":
        return OllamaProvider(**kwargs)
    if name == "anthropic":
        return AnthropicProvider(**kwargs)
    if name == "openai":
        return OpenAIProvider(**kwargs)
    raise ValueError(
        f"unknown provider {name!r}. Available: ollama, anthropic, openai."
    )
