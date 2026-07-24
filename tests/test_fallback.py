"""Provider-fallback behavior for LLMClient.chat().

Acceptance criteria for W8:
- primary raises -> fallback answers -> provider_used == fallback
- no fallback configured -> original exception propagates
- both fail -> original exception propagates (NOT the fallback's)
- LLM_FALLBACK_PROVIDER validation: same as primary, invalid name, missing
  API key all disable fallback (raise original).
"""

from unittest.mock import Mock

import pytest

from src.llm_client import LLMClient


def _stub_provider(client: LLMClient, name: str, *, raises: Exception | None = None,
                   content: str = "ok") -> Mock:
    """Replace client._chat_<name> with a stub. By default returns a benign
    payload; if `raises` is set, the stub raises that exception."""
    method = Mock(
        side_effect=raises if raises is not None else None,
        return_value=None if raises is not None else {"content": content, "tool_calls": []},
    )
    # Mock(side_effect=...) returns the side_effect's value from the call,
    # ignoring return_value. For a "raises" stub that's correct. For a
    # success stub we want return_value, so re-bind without side_effect.
    if raises is None:
        method = Mock(return_value={"content": content, "tool_calls": []})
    setattr(client, f"_chat_{name}", method)
    return method


# --- Happy paths -----------------------------------------------------------


def test_fallback_used_when_primary_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    client = LLMClient()
    primary = _stub_provider(client, "anthropic", raises=RuntimeError("rate limit"))
    fallback = _stub_provider(client, "openai", content="from-openai")

    result = client.chat([{"role": "user", "content": "hi"}])

    primary.assert_called_once()
    fallback.assert_called_once()
    assert result["content"] == "from-openai"
    assert result["tool_calls"] == []
    assert result["provider_used"] == "openai"


def test_fallback_not_used_when_primary_succeeds(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    client = LLMClient()
    primary = _stub_provider(client, "anthropic", content="from-anthropic")
    fallback = _stub_provider(client, "openai", content="from-openai")

    result = client.chat([])

    primary.assert_called_once()
    fallback.assert_not_called()
    assert result["content"] == "from-anthropic"
    assert result["provider_used"] == "anthropic"


def test_fallback_retries_with_same_arguments(monkeypatch):
    """The same request must be retried on the fallback - same messages,
    system, and tools. Otherwise the fallback would silently answer a
    different question than the one that failed."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "minimax")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")

    client = LLMClient()
    _stub_provider(client, "anthropic", raises=RuntimeError("down"))
    fallback = _stub_provider(client, "minimax", content="ok")

    msgs = [{"role": "user", "content": "explain FNS"}]
    client.chat(messages=msgs, system="be terse", tools=[{"name": "lookup"}])

    fallback.assert_called_once_with(msgs, "be terse", [{"name": "lookup"}])


# --- No-fallback paths -----------------------------------------------------


def test_no_fallback_propagates_original_exception(monkeypatch):
    """Without LLM_FALLBACK_PROVIDER set, a primary failure must propagate
    exactly as before - no retry, no extra key on the exception path."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    client = LLMClient()
    primary = _stub_provider(client, "anthropic", raises=RuntimeError("boom"))
    fallback = _stub_provider(client, "openai", content="should-not-run")

    with pytest.raises(RuntimeError, match="boom"):
        client.chat([])

    primary.assert_called_once()
    fallback.assert_not_called()


def test_empty_fallback_env_disables_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "")  # explicitly empty
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    client = LLMClient()
    primary = _stub_provider(client, "anthropic", raises=RuntimeError("boom"))
    fallback = _stub_provider(client, "openai", content="should-not-run")

    with pytest.raises(RuntimeError, match="boom"):
        client.chat([])
    fallback.assert_not_called()


def test_fallback_equal_to_primary_is_ignored(monkeypatch):
    """Config typo guard: if someone sets LLM_FALLBACK_PROVIDER to the same
    provider, don't loop (we'd recurse). Treat as no-fallback and raise."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    client = LLMClient()
    primary = _stub_provider(client, "anthropic", raises=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        client.chat([])
    primary.assert_called_once()


def test_fallback_with_invalid_name_is_ignored(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "not-a-provider")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    client = LLMClient()
    primary = _stub_provider(client, "anthropic", raises=RuntimeError("boom"))
    fallback = _stub_provider(client, "openai", content="should-not-run")

    with pytest.raises(RuntimeError, match="boom"):
        client.chat([])
    fallback.assert_not_called()


def test_fallback_without_api_key_is_ignored(monkeypatch):
    """If the fallback provider's API key is missing, retrying would just
    produce a different error. Treat as no-fallback configured."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = LLMClient()
    primary = _stub_provider(client, "anthropic", raises=RuntimeError("boom"))
    fallback = _stub_provider(client, "openai", content="should-not-run")

    with pytest.raises(RuntimeError, match="boom"):
        client.chat([])
    fallback.assert_not_called()


# --- Both fail -------------------------------------------------------------


def test_both_providers_failing_raises_original_exception(monkeypatch):
    """When both primary and fallback fail, the ORIGINAL primary exception
    propagates. Callers see the same error they'd see with no fallback -
    the fallback's failure is operational noise, not the signal."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    client = LLMClient()
    primary_exc = RuntimeError("primary rate limit")
    fallback_exc = RuntimeError("fallback quota exceeded")
    _stub_provider(client, "anthropic", raises=primary_exc)
    fallback = _stub_provider(client, "openai", raises=fallback_exc)

    with pytest.raises(RuntimeError) as exc_info:
        client.chat([])

    # Same exception object (not just same type) - so any "primary failed"
    # logging in upstream code still works.
    assert exc_info.value is primary_exc
    fallback.assert_called_once()


# --- Cross-provider rotation ---------------------------------------------


@pytest.mark.parametrize("primary,fallback", [
    ("anthropic", "openai"),
    ("anthropic", "minimax"),
    ("anthropic", "openrouter"),
    ("openai", "anthropic"),
    ("openai", "minimax"),
    ("minimax", "anthropic"),
    ("minimax", "openai"),
    ("minimax", "openrouter"),
    ("openrouter", "minimax"),
])
def test_fallback_rotates_any_pair(monkeypatch, primary, fallback):
    monkeypatch.setenv("LLM_PROVIDER", primary)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", fallback)
    for name in ("anthropic", "openai", "minimax", "openrouter"):
        monkeypatch.setenv(f"{name.upper()}_API_KEY", "sk-test")

    client = LLMClient()
    _stub_provider(client, primary, raises=RuntimeError("down"))
    fb = _stub_provider(client, fallback, content=f"from-{fallback}")

    result = client.chat([])
    fb.assert_called_once()
    assert result["provider_used"] == fallback
    assert result["content"] == f"from-{fallback}"
