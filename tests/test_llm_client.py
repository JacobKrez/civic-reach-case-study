import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.llm_client import LLMClient


def test_dispatches_anthropic_and_normalizes(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    client = LLMClient()
    method = Mock(return_value={"content": "anthropic", "tool_calls": []})
    monkeypatch.setattr(client, "_chat_anthropic", method)
    result = client.chat([{"role": "user", "content": "hi"}])
    method.assert_called_once()
    assert result == {"content": "anthropic", "tool_calls": []}


def test_dispatches_openai_and_normalizes(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    client = LLMClient()
    method = Mock(return_value={"content": "openai"})
    monkeypatch.setattr(client, "_chat_openai", method)
    result = client.chat([])
    method.assert_called_once()
    assert result == {"content": "openai", "tool_calls": []}


def test_dispatches_minimax_and_normalizes(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    client = LLMClient()
    method = Mock(return_value={"tool_calls": [{"name": "lookup", "input": {}}]})
    monkeypatch.setattr(client, "_chat_minimax", method)
    result = client.chat([])
    method.assert_called_once()
    assert result == {
        "content": "",
        "tool_calls": [{"name": "lookup", "input": {}}],
    }


def test_invalid_provider_lists_valid_options(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "other")
    with pytest.raises(ValueError) as exc:
        LLMClient().chat([])
    message = str(exc.value)
    assert all(provider in message for provider in ("anthropic", "openai", "minimax"))


def test_anthropic_sdk_is_mocked(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="hello"),
            SimpleNamespace(type="tool_use", name="search", input={"q": "x"}),
        ]
    )
    create = Mock(return_value=response)
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", Mock(return_value=fake_client))
    result = LLMClient()._chat_anthropic([], "system", [])
    assert result == {
        "content": "hello",
        "tool_calls": [{"name": "search", "input": {"q": "x"}}],
    }
    create.assert_called_once()


def test_openai_sdk_is_mocked(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    call = SimpleNamespace(
        function=SimpleNamespace(name="search", arguments=json.dumps({"q": "x"}))
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello", tool_calls=[call]))]
    )
    create = Mock(return_value=response)
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    import openai

    monkeypatch.setattr(openai, "OpenAI", Mock(return_value=fake_client))
    result = LLMClient()._chat_openai([], None, None)
    assert result == {
        "content": "hello",
        "tool_calls": [{"name": "search", "input": {"q": "x"}}],
    }
    create.assert_called_once()


def test_minimax_requests_is_mocked(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "minimax-test")
    response = Mock()
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "hello",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "search",
                                "arguments": json.dumps({"q": "x"}),
                            }
                        }
                    ],
                }
            }
        ]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr("src.llm_client.requests.post", post)
    result = LLMClient()._chat_minimax([], None, None)
    assert result == {
        "content": "hello",
        "tool_calls": [{"name": "search", "input": {"q": "x"}}],
    }
    response.raise_for_status.assert_called_once()
    post.assert_called_once()
