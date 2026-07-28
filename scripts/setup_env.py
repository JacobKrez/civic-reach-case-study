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
PROVIDERS: dict[str, tuple[str, str]] = {
    "1": ("anthropic", "ANTHROPIC_API_KEY"),
    "2": ("openai", "OPENAI_API_KEY"),
    "3": ("minimax", "MINIMAX_API_KEY"),
    "4": ("openrouter", "OPENROUTER_API_KEY"),
}
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
    print("FoodPath needs exactly one of the following:")
    print("  1) Anthropic   (ANTHROPIC_API_KEY)")
    print("  2) OpenAI      (OPENAI_API_KEY)")
    print("  3) MiniMax     (MINIMAX_API_KEY)")
    print("  4) OpenRouter  (OPENROUTER_API_KEY)")
    print()
    choice = ""
    while choice not in PROVIDERS:
        choice = input("Choose a provider [1-4]: ").strip()
    provider, key_name = PROVIDERS[choice]

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
