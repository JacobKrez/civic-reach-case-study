"""Interactive .env setup for a first-time run.

Called by ``setup.bat`` (Windows) and ``setup.sh`` (macOS/Linux) after the
virtualenv is created and dependencies are installed. Also safe to run
standalone: ``python scripts/setup_env.py``.

Idempotent by design: if .env already has a provider key configured, this
exits immediately without touching anything or prompting -- safe to call
on every run of the setup script, not just the first.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# (provider name for LLM_PROVIDER=, the .env key it needs)
#
# Kept in the order a reviewer is most likely to already have access to.
# Ollama is last and is the no-account option: it runs a model on the
# reviewer's own machine, so the app is runnable with no vendor
# relationship at all.
PROVIDERS: dict[str, tuple[str, str]] = {
    "1": ("anthropic", "ANTHROPIC_API_KEY"),
    "2": ("openai", "OPENAI_API_KEY"),
    "3": ("google", "GOOGLE_API_KEY"),
    "4": ("groq", "GROQ_API_KEY"),
    "5": ("openrouter", "OPENROUTER_API_KEY"),
    "6": ("deepseek", "DEEPSEEK_API_KEY"),
    "7": ("minimax", "MINIMAX_API_KEY"),
    "8": ("ollama", "OLLAMA_HOST"),
}

# Providers that need no API key. The value written to .env is a
# placeholder so every downstream key-presence check (the CLI's
# pre-flight, LLMClient._has_api_key, has_configured_key below) passes
# without any of them needing an ollama-shaped special case.
KEYLESS_PROVIDERS = {"ollama": "local"}
_ALL_KEY_NAMES = {name for _, name in PROVIDERS.values()}


def ensure_env_file_exists() -> list[str]:
    """Return .env's lines, creating it from .env.example if missing."""
    if not ENV_PATH.exists():
        shutil.copy(ENV_EXAMPLE_PATH, ENV_PATH)
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def has_configured_key(lines: list[str]) -> bool:
    """True if any of the four provider key lines already has a value."""
    for line in lines:
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in _ALL_KEY_NAMES and value.strip():
            return True
    return False


def apply_provider_choice(
    lines: list[str], provider: str, key_name: str, api_key: str
) -> list[str]:
    """Return .env's lines with LLM_PROVIDER and the chosen key line set.

    Replaces existing LLM_PROVIDER=/<key_name>= lines in place so the
    rest of the file (comments, the other three empty key slots) is
    left exactly as .env.example shipped it. Appends either line if it
    was missing entirely (defensive -- .env.example already has both).
    """
    out: list[str] = []
    seen_key = seen_provider = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key_name}="):
            out.append(f"{key_name}={api_key}")
            seen_key = True
            continue
        if stripped.startswith("LLM_PROVIDER="):
            out.append(f"LLM_PROVIDER={provider}")
            seen_provider = True
            continue
        out.append(line)
    if not seen_key:
        out.append(f"{key_name}={api_key}")
    if not seen_provider:
        out.append(f"LLM_PROVIDER={provider}")
    return out


def prompt_for_provider_and_key() -> tuple[str, str, str]:
    """Interactively ask which provider and key. Returns (provider, key_name, api_key)."""
    print()
    print("No LLM provider key found in .env yet.")
    print("FoodPath needs exactly ONE of the following. Pick whichever")
    print("you already have access to -- the agent works with any of them.")
    print()
    print("  1) Anthropic   (ANTHROPIC_API_KEY)   console.anthropic.com")
    print("  2) OpenAI      (OPENAI_API_KEY)      platform.openai.com")
    print("  3) Google      (GOOGLE_API_KEY)      aistudio.google.com  [free tier]")
    print("  4) Groq        (GROQ_API_KEY)        console.groq.com     [free tier]")
    print("  5) OpenRouter  (OPENROUTER_API_KEY)  openrouter.ai        [one key, many models]")
    print("  6) DeepSeek    (DEEPSEEK_API_KEY)    platform.deepseek.com")
    print("  7) MiniMax     (MINIMAX_API_KEY)     minimax.io")
    print("  8) Ollama      (no key needed)       runs locally -- see note below")
    print()
    print("  Note on 8: Ollama runs the model on this machine, so it needs no")
    print("  account and no key. Install it from ollama.com, then run")
    print("  'ollama pull llama3.1' before starting. Pick a model that supports")
    print("  tool calling -- FoodPath records every answer through a tool call.")
    print()
    choice = ""
    while choice not in PROVIDERS:
        choice = input(f"Choose a provider [1-{len(PROVIDERS)}]: ").strip()
    provider, key_name = PROVIDERS[choice]

    if provider in KEYLESS_PROVIDERS:
        print(f"\n{provider} needs no API key -- configuring it now.")
        return provider, key_name, KEYLESS_PROVIDERS[provider]

    api_key = ""
    while not api_key:
        api_key = input(f"Paste your {provider} API key: ").strip()
    return provider, key_name, api_key


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    check_only = "--check-only" in argv

    lines = ensure_env_file_exists()
    if has_configured_key(lines):
        print(".env already has a provider key configured -- skipping setup.")
        return 0

    if check_only:
        # Used by CI: verify the file-creation / detection logic works on
        # a real machine without requiring interactive input.
        print(".env has no provider key yet (expected on a fresh clone).")
        return 0

    provider, key_name, api_key = prompt_for_provider_and_key()
    lines = apply_provider_choice(lines, provider, key_name, api_key)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f".env configured for {provider}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
