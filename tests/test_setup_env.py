"""Tripwires for scripts/setup_env.py -- the .env half of the one-file
Windows/macOS/Linux setup scripts. Runs entirely against a temp directory;
never touches the real .env.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def setup_env_module(tmp_path, monkeypatch):
    """Import scripts.setup_env with REPO_ROOT/ENV_PATH pointed at a
    scratch directory, so the tests can never touch the real .env."""
    example = tmp_path / ".env.example"
    example.write_text(
        "ANTHROPIC_API_KEY=\n"
        "OPENAI_API_KEY=\n"
        "MINIMAX_API_KEY=\n"
        "OPENROUTER_API_KEY=\n"
        "LLM_PROVIDER=anthropic\n"
        "LLM_FALLBACK_PROVIDER=\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import scripts.setup_env as mod

    importlib.reload(mod)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(mod, "ENV_EXAMPLE_PATH", example)
    yield mod
    (tmp_path / ".env").unlink(missing_ok=True)


def test_creates_env_from_example_when_missing(setup_env_module):
    mod = setup_env_module
    assert not mod.ENV_PATH.exists()
    lines = mod.ensure_env_file_exists()
    assert mod.ENV_PATH.exists()
    assert any(l.startswith("ANTHROPIC_API_KEY=") for l in lines)


def test_leaves_existing_env_untouched(setup_env_module):
    mod = setup_env_module
    mod.ENV_PATH.write_text("MINIMAX_API_KEY=already-here\n", encoding="utf-8")
    lines = mod.ensure_env_file_exists()
    assert lines == ["MINIMAX_API_KEY=already-here"]


@pytest.mark.parametrize(
    "lines,expected",
    [
        (["ANTHROPIC_API_KEY=", "OPENAI_API_KEY="], False),
        (["ANTHROPIC_API_KEY=sk-abc123"], True),
        (["MINIMAX_API_KEY=   "], False),  # whitespace-only is not configured
        (["# comment", "OPENROUTER_API_KEY=or-xyz"], True),
        ([], False),
    ],
)
def test_has_configured_key(setup_env_module, lines, expected):
    assert setup_env_module.has_configured_key(lines) is expected


def test_apply_provider_choice_replaces_in_place_preserving_the_rest(setup_env_module):
    mod = setup_env_module
    lines = [
        "ANTHROPIC_API_KEY=",
        "OPENAI_API_KEY=",
        "MINIMAX_API_KEY=",
        "OPENROUTER_API_KEY=",
        "LLM_PROVIDER=anthropic",
        "LLM_FALLBACK_PROVIDER=",
    ]
    out = mod.apply_provider_choice(lines, "minimax", "MINIMAX_API_KEY", "sk-test-key")
    assert "MINIMAX_API_KEY=sk-test-key" in out
    assert "LLM_PROVIDER=minimax" in out
    # Everything else survives untouched, same order.
    assert "ANTHROPIC_API_KEY=" in out
    assert "OPENAI_API_KEY=" in out
    assert "OPENROUTER_API_KEY=" in out
    assert "LLM_FALLBACK_PROVIDER=" in out
    assert len(out) == len(lines)


def test_apply_provider_choice_appends_if_lines_were_missing(setup_env_module):
    mod = setup_env_module
    out = mod.apply_provider_choice([], "openai", "OPENAI_API_KEY", "sk-1")
    assert "OPENAI_API_KEY=sk-1" in out
    assert "LLM_PROVIDER=openai" in out


def test_main_check_only_never_prompts_and_exits_zero_on_fresh_clone(setup_env_module, capsys):
    mod = setup_env_module
    rc = mod.main(["--check-only"])
    assert rc == 0
    assert "expected on a fresh clone" in capsys.readouterr().out


def test_main_check_only_exits_zero_when_a_key_is_already_configured(setup_env_module):
    mod = setup_env_module
    mod.ENV_PATH.write_text("ANTHROPIC_API_KEY=sk-already-set\n", encoding="utf-8")
    rc = mod.main(["--check-only"])
    assert rc == 0


def test_main_prompts_and_writes_the_key_end_to_end(setup_env_module, monkeypatch):
    mod = setup_env_module
    # Menu position is looked up rather than hardcoded: the list is
    # ordered by how likely a reviewer is to have each provider, so it
    # will be reordered again. What must hold is that choosing MiniMax
    # writes MiniMax's key and names MiniMax as the provider.
    choice = next(k for k, (name, _key) in mod.PROVIDERS.items() if name == "minimax")
    answers = iter([choice, "sk-my-minimax-key"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = mod.main([])
    assert rc == 0
    written = mod.ENV_PATH.read_text(encoding="utf-8")
    assert "MINIMAX_API_KEY=sk-my-minimax-key" in written
    assert "LLM_PROVIDER=minimax" in written


def test_main_rejects_an_out_of_range_choice_before_accepting(setup_env_module, monkeypatch):
    """A fat-fingered number past the end of the menu, or "", must
    re-prompt rather than crash or silently pick a provider."""
    mod = setup_env_module
    past_the_end = str(len(mod.PROVIDERS) + 1)
    choice = next(k for k, (name, _key) in mod.PROVIDERS.items() if name == "openai")
    answers = iter([past_the_end, "", choice, "sk-openai-key"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = mod.main([])
    assert rc == 0
    written = mod.ENV_PATH.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-openai-key" in written
    assert "LLM_PROVIDER=openai" in written


def test_keyless_provider_configures_without_prompting_for_a_key(
    setup_env_module, monkeypatch
):
    """Ollama runs locally and has no key to paste.

    Setup must not sit waiting for one -- a prompt that cannot be
    satisfied is an unfinishable install. A placeholder is written so
    every downstream key-presence check passes unchanged.
    """
    mod = setup_env_module
    choice = next(k for k, (name, _key) in mod.PROVIDERS.items() if name == "ollama")
    answers = iter([choice])  # exactly one answer: no key prompt may follow
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = mod.main([])
    assert rc == 0
    written = mod.ENV_PATH.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=ollama" in written
    assert "OLLAMA_HOST=local" in written


def test_every_menu_provider_is_one_the_client_accepts(setup_env_module):
    """Setup and the client must not drift: a menu entry the client
    rejects produces a .env that cannot start the app."""
    from src.llm_client import LLMClient

    for _choice, (provider, key_name) in setup_env_module.PROVIDERS.items():
        assert provider in LLMClient.VALID_PROVIDERS, provider
        assert LLMClient.PROVIDER_KEY_ENV[provider] == key_name, provider


def test_main_rejects_a_blank_key_before_accepting(setup_env_module, monkeypatch):
    answers = iter(["1", "   ", "sk-real-key"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = setup_env_module.main([])
    assert rc == 0
    written = setup_env_module.ENV_PATH.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-real-key" in written
