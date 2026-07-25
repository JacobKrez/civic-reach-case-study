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
    assert result == {
        "content": "anthropic",
        "tool_calls": [],
        "provider_used": "anthropic",
    }


def test_dispatches_openai_and_normalizes(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    client = LLMClient()
    method = Mock(return_value={"content": "openai"})
    monkeypatch.setattr(client, "_chat_openai", method)
    result = client.chat([])
    method.assert_called_once()
    assert result == {
        "content": "openai",
        "tool_calls": [],
        "provider_used": "openai",
    }


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
        "provider_used": "minimax",
    }


def test_dispatches_openrouter_and_normalizes(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    client = LLMClient()
    method = Mock(return_value={"content": "openrouter", "tool_calls": []})
    monkeypatch.setattr(client, "_chat_openrouter", method)
    result = client.chat([{"role": "user", "content": "hi"}])
    method.assert_called_once()
    assert result == {
        "content": "openrouter",
        "tool_calls": [],
        "provider_used": "openrouter",
    }


def test_invalid_provider_lists_valid_options(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "other")
    with pytest.raises(ValueError) as exc:
        LLMClient().chat([])
    message = str(exc.value)
    assert all(provider in message for provider in LLMClient.VALID_PROVIDERS)


def test_cli_provider_map_matches_client():
    """W28: CLI's _PROVIDER_KEY_ENV must cover exactly the same provider set
    as LLMClient.VALID_PROVIDERS. Otherwise a reviewer following README.md's
    own OPENROUTER quickstart hits 'invalid or missing LLM_PROVIDER' before
    LLMClient is ever constructed (regression guard)."""
    from src import cli

    cli_map = getattr(cli, "_PROVIDER_KEY_ENV", None)
    assert cli_map is not None, "cli._PROVIDER_KEY_ENV must exist"
    assert set(cli_map) == set(LLMClient.VALID_PROVIDERS), (
        "provider maps drift: cli=%s client=%s"
        % (set(cli_map), set(LLMClient.VALID_PROVIDERS))
    )
    # And every value must be a real env var name, not None/empty.
    for provider, env_var in cli_map.items():
        assert env_var and isinstance(env_var, str), (
            f"cli._PROVIDER_KEY_ENV[{provider!r}] must be a non-empty env var name, got {env_var!r}"
        )


def test_default_models_resolve_on_each_provider(monkeypatch):
    """W28: each id in DEFAULT_MODELS must be a current model on its
    provider. We can't hit the live APIs from CI (no keys), so we verify
    structural sanity: every value is a non-empty string, the dict keys
    match VALID_PROVIDERS, and anthropic / openai ids follow the
    documented id conventions of those vendors. The OpenRouter id is
    also checked against OpenRouter's live /models listing (cached on
    the worker's session) — see test_default_openrouter_model_is_listed.
    """
    assert set(LLMClient.DEFAULT_MODELS) == set(LLMClient.VALID_PROVIDERS)
    for provider, model_id in LLMClient.DEFAULT_MODELS.items():
        assert isinstance(model_id, str) and model_id, (
            f"DEFAULT_MODELS[{provider!r}] must be a non-empty string, got {model_id!r}"
        )
    # Anthropic model ids are 'claude-{family}-{major}' (e.g. claude-sonnet-4-5)
    assert LLMClient.DEFAULT_MODELS["anthropic"].startswith("claude-"), \
        f"unexpected anthropic model id: {LLMClient.DEFAULT_MODELS['anthropic']!r}"
    # OpenAI model ids are 'gpt-...' or 'o...' (reasoning series)
    oai = LLMClient.DEFAULT_MODELS["openai"]
    assert oai.startswith(("gpt-", "o")), f"unexpected openai model id: {oai!r}"
    # OpenRouter ids are vendor/model (slash-separated)
    or_id = LLMClient.DEFAULT_MODELS["openrouter"]
    assert "/" in or_id, f"openrouter id should be vendor/model, got {or_id!r}"


def test_default_openrouter_model_is_listed():
    """W28: moonshotai/kimi-k3 must appear on OpenRouter's live model
    listing. Smoke-test only — OpenRouter adds/drops models often, and
    we don't want a CI dependency on their uptime. If this fails, the
    default is stale and needs refreshing (see chunk W28)."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            "https://openrouter.ai/api/v1/models", timeout=15
        ) as resp:
            import json as _json

            payload = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        pytest.skip("OpenRouter /models unreachable from this environment")
    listed = {m.get("id") for m in payload.get("data", [])}
    assert LLMClient.DEFAULT_MODELS["openrouter"] in listed, (
        f"default openrouter model {LLMClient.DEFAULT_MODELS['openrouter']!r} "
        f"is not on OpenRouter's live /models listing"
    )


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


def test_minimax_falls_back_to_reasoning_content_when_content_empty(monkeypatch):
    """When MiniMax's reasoning model returns empty content with a
    reasoning_content field, the user-facing assistant text must not be
    empty -- the agent loop's outcome-term backstop only fires after
    extraction produces content, and a chat whose final reply is
    just a single space triggers the universal_invariants failure
    (no outcome term in any reply)."""
    monkeypatch.setenv("LLM_MODEL", "minimax-test")
    response = Mock()
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_content": "Final answer: likely eligible.",
                    "tool_calls": [],
                }
            }
        ]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr("src.llm_client.requests.post", post)
    result = LLMClient()._chat_minimax([], None, None)
    assert result == {
        "content": "Final answer: likely eligible.",
        "tool_calls": [],
    }


def test_minimax_keeps_content_when_both_present(monkeypatch):
    """If MiniMax returns both content and reasoning_content, content
    wins -- reasoning_content is the model's internal chain-of-thought
    and must NOT replace the user-facing text."""
    monkeypatch.setenv("LLM_MODEL", "minimax-test")
    response = Mock()
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "hello",
                    "reasoning_content": "thinking...",
                    "tool_calls": [],
                }
            }
        ]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr("src.llm_client.requests.post", post)
    result = LLMClient()._chat_minimax([], None, None)
    assert result == {
        "content": "hello",
        "tool_calls": [],
    }


def test_openrouter_requests_is_mocked(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openrouter-test")
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
    result = LLMClient()._chat_openrouter([], None, None)
    assert result == {
        "content": "hello",
        "tool_calls": [{"name": "search", "input": {"q": "x"}}],
    }
    response.raise_for_status.assert_called_once()
    post.assert_called_once()
