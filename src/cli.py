"""Command-line REPL for the CivicReach agent.

Run with::

    python3 -m src.cli

Reads one line at a time from stdin, passes each line to
``CivicReachAgent.turn``, and prints the assistant reply. Handles
``KeyboardInterrupt`` (Ctrl-C) and end-of-file input gracefully so the
process never crashes with a traceback on trivial input during a live demo.
Exits with status 1 when no LLM provider API key is configured.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from .agent import CivicReachAgent
from .llm_client import LLMClient


_BANNER = (
    "CivicReach \u2014 NC Food & Nutrition Services (FNS) eligibility assistant.\n"
    "This is an initial screen, not a final agency determination.\n"
    "Type your situation and press Enter. Press Ctrl-C or send an empty line to exit."
)


_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


def _missing_key_error(provider: str) -> str | None:
    """Return a one-line human error if the active provider has no key, else None."""
    provider = provider.strip().lower()
    env_var = _PROVIDER_KEY_ENV.get(provider)
    if env_var is None:
        valid = ", ".join(sorted(_PROVIDER_KEY_ENV))
        return f"Error: invalid or missing LLM_PROVIDER (got {provider!r}); expected one of: {valid}."
    if not os.getenv(env_var, "").strip():
        return f"Error: environment variable {env_var} is not set (LLM_PROVIDER={provider!r})."
    return None


def _run_repl(agent: CivicReachAgent, input_stream=sys.stdin, output=sys.stdout) -> int:
    print(_BANNER, file=output)
    output.flush()
    while True:
        try:
            line = input_stream.readline()
        except KeyboardInterrupt:
            print("\nGoodbye.", file=output)
            return 0
        if not line:
            # EOF: stdin closed or empty input piped in.
            print("Goodbye.", file=output)
            return 0
        user_message = line.rstrip("\n").rstrip("\r")
        if not user_message.strip():
            print("Goodbye.", file=output)
            return 0
        try:
            reply = agent.turn(user_message)
        except KeyboardInterrupt:
            print("\nGoodbye.", file=output)
            return 0
        except Exception as exc:  # noqa: BLE001 \u2014 surface error, never traceback
            print(f"Agent error: {exc}", file=output)
            continue
        print(reply, file=output)
        output.flush()


def main() -> int:
    # See LLMClient._ENV_KEYS for why this loop exists (an empty ambient
    # env var, not just a missing one, must not shadow the repo's .env).
    for key in LLMClient._ENV_KEYS:
        if os.environ.get(key) == "":
            del os.environ[key]
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    error = _missing_key_error(provider)
    if error is not None:
        print(error)
        return 1
    try:
        client = LLMClient()
    except KeyboardInterrupt:
        return 0
    agent = CivicReachAgent(llm_client=client)
    try:
        return _run_repl(agent)
    except KeyboardInterrupt:
        print("Goodbye.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
