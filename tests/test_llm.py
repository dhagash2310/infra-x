"""Tests for the LLM provider layer.

We mock httpx.post so the tests run with no network and no API key required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from infra_x.llm import (
    AnthropicProvider,
    OllamaProvider,
    OpenAIProvider,
    get_provider,
)
from infra_x.llm.anthropic import _extract_first_json_object as anthropic_extract


# --- factory ----------------------------------------------------------------


def test_get_provider_ollama_is_default():
    p = get_provider()
    assert isinstance(p, OllamaProvider)


def test_get_provider_resolves_anthropic():
    p = get_provider("anthropic", api_key="sk-test")
    assert isinstance(p, AnthropicProvider)
    assert p.api_key == "sk-test"


def test_get_provider_resolves_openai():
    p = get_provider("openai", api_key="sk-test")
    assert isinstance(p, OpenAIProvider)
    assert p.api_key == "sk-test"


def test_get_provider_is_case_insensitive():
    assert isinstance(get_provider("Anthropic", api_key="x"), AnthropicProvider)
    assert isinstance(get_provider("OPENAI", api_key="x"), OpenAIProvider)


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider("does-not-exist")


# --- Anthropic --------------------------------------------------------------


def _mock_response(status: int = 200, json_body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body or {}
    r.raise_for_status = MagicMock()
    if status >= 400:
        err = httpx.HTTPStatusError("err", request=MagicMock(), response=r)
        r.text = '{"error": "boom"}'
        r.raise_for_status.side_effect = err
    return r


def test_anthropic_complete_sends_correct_request():
    p = AnthropicProvider(api_key="sk-ant-test", model="claude-sonnet-4-6")
    fake = _mock_response(200, {
        "content": [{"type": "text", "text": "hello there"}],
        "id": "msg_123",
    })
    with patch("infra_x.llm.anthropic.httpx.post", return_value=fake) as mock_post:
        resp = p.complete(system="You are X", user="say hi")

    assert resp.content == "hello there"
    assert resp.parsed is None  # not json_mode
    # Verify request shape
    call = mock_post.call_args
    assert call.kwargs["headers"]["x-api-key"] == "sk-ant-test"
    assert call.kwargs["headers"]["anthropic-version"] == "2023-06-01"
    assert "/v1/messages" in call.args[0]
    body = call.kwargs["json"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["system"] == "You are X"
    assert body["messages"] == [{"role": "user", "content": "say hi"}]


def test_anthropic_json_mode_prefills_and_parses():
    """JSON mode should prefill `{` and reassemble the response into valid JSON."""
    p = AnthropicProvider(api_key="sk-ant-test")
    # Anthropic returns the continuation AFTER the prefill, without the leading `{`.
    fake = _mock_response(200, {
        "content": [{"type": "text", "text": '"name": "todos", "env": "prod"}'}],
    })
    with patch("infra_x.llm.anthropic.httpx.post", return_value=fake) as mock_post:
        resp = p.complete(system="Return JSON", user="...", json_mode=True)

    assert resp.parsed == {"name": "todos", "env": "prod"}
    # Verify the prefill was sent
    body = mock_post.call_args.kwargs["json"]
    assert body["messages"][-1] == {"role": "assistant", "content": "{"}


def test_anthropic_missing_api_key_raises_helpful_error():
    p = AnthropicProvider(api_key=None)
    # Make sure env var doesn't leak in
    with patch.dict("os.environ", {}, clear=False):
        p.api_key = None
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
            p.complete(system="x", user="y")


def test_anthropic_http_error_includes_body():
    p = AnthropicProvider(api_key="sk-test")
    fake = _mock_response(401)
    with patch("infra_x.llm.anthropic.httpx.post", return_value=fake):
        with pytest.raises(RuntimeError, match="401"):
            p.complete(system="x", user="y")


def test_anthropic_extract_first_json_object():
    """Helper recovers JSON even when wrapped in prose."""
    s = 'sure! here it is: {"a": 1, "b": 2} cheers'
    assert anthropic_extract(s) == {"a": 1, "b": 2}


# --- OpenAI -----------------------------------------------------------------


def test_openai_complete_sends_correct_request():
    p = OpenAIProvider(api_key="sk-openai-test", model="gpt-4o-mini")
    fake = _mock_response(200, {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    })
    with patch("infra_x.llm.openai.httpx.post", return_value=fake) as mock_post:
        resp = p.complete(system="be terse", user="ping")

    assert resp.content == "ok"
    assert resp.parsed is None
    call = mock_post.call_args
    assert call.kwargs["headers"]["Authorization"] == "Bearer sk-openai-test"
    assert "/v1/chat/completions" in call.args[0]
    body = call.kwargs["json"]
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "ping"},
    ]


def test_openai_json_mode_sets_response_format_and_parses():
    p = OpenAIProvider(api_key="sk-test")
    fake = _mock_response(200, {
        "choices": [{"message": {"content": '{"a": 1, "b": [2, 3]}'}}],
    })
    with patch("infra_x.llm.openai.httpx.post", return_value=fake) as mock_post:
        resp = p.complete(system="x", user="y", json_mode=True)

    assert resp.parsed == {"a": 1, "b": [2, 3]}
    body = mock_post.call_args.kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}


def test_openai_missing_api_key_raises():
    p = OpenAIProvider(api_key=None)
    p.api_key = None  # belt-and-suspenders for env-var resolution
    with patch.dict("os.environ", {}, clear=False):
        p.api_key = None
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
            p.complete(system="x", user="y")


def test_openai_http_error_includes_status():
    p = OpenAIProvider(api_key="sk-test")
    fake = _mock_response(429)
    with patch("infra_x.llm.openai.httpx.post", return_value=fake):
        with pytest.raises(RuntimeError, match="429"):
            p.complete(system="x", user="y")


def test_openai_handles_empty_choices_gracefully():
    """Some error responses come back with status 200 but no choices."""
    p = OpenAIProvider(api_key="sk-test")
    fake = _mock_response(200, {"choices": []})
    with patch("infra_x.llm.openai.httpx.post", return_value=fake):
        resp = p.complete(system="x", user="y")
    assert resp.content == ""
