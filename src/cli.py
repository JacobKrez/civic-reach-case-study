"""Command-line REPL for the FoodPath agent.

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

from .agent import FoodPathAgent, first_turn_disclosure, first_turn_greeting
from .llm_client import LLMClient


# W21 — the CLI banner is augmented with the same AI / not-a-person
# disclosure the web UI surfaces as its first two assistant bubbles, so
# the REPL also makes it impossible for a user to confuse FoodPath for
# a live person / caseworker / hotline before they type anything. Shown
# as two visually separated paragraphs (safety disclosure, then a short
# friendly opener) rather than one block, matching the web UI's two
# bubbles. Both pieces are hard-coded / code-templated, identical to the
# web UI's, so the framing cannot drift across surfaces.
_BANNER = (
    first_turn_disclosure("en")
    + "\n\n"
    + first_turn_greeting("en")
    + (
        "\n\n--- Repl controls ---\n"
        "Type 'handoff' for a case-worker handoff summary once the screen "
        "is complete. Type 'new' or 'restart' to erase this screening and "
        "start over. Type 'exit' or press Ctrl-C to leave."
    )
)


# W28: derive from LLMClient.PROVIDER_KEY_ENV rather than duplicating it,
# so adding a provider in one place (LLMClient) updates the CLI's
# pre-construction validation automatically. Keep the alias because
# tests assert set(_PROVIDER_KEY_ENV) == set(VALID_PROVIDERS) — see
# tests/test_llm_client.py::test_cli_provider_map_matches_client.
_PROVIDER_KEY_ENV = LLMClient.PROVIDER_KEY_ENV


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


def _run_repl(agent: FoodPathAgent, input_stream=sys.stdin, output=sys.stdout) -> int:
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
        if user_message.strip().lower() in {"exit", "quit"}:
            print("Goodbye.", file=output)
            return 0
        if user_message.strip().lower() in {"new", "restart"}:
            # Keep the configured provider client and retriever, but replace
            # every piece of conversation/assessment state with a fresh
            # agent—exactly the same lifecycle boundary as POST
            # /session/reset in the web surface.
            agent = type(agent)(
                llm_client=agent.llm_client,
                retriever=agent._retrieve,
            )
            print(
                "\n--- New screening started. Previous answers were erased. ---\n",
                file=output,
            )
            print(first_turn_disclosure("en"), file=output)
            print(f"\n{first_turn_greeting('en')}", file=output)
            output.flush()
            continue
        if user_message.strip().lower() == "handoff":
            # W17: print-ready case-worker handoff summary. Purely local —
            # renders the deterministic screen result; nothing is
            # submitted anywhere. Email is best-effort only when SMTP_*
            # env vars are all present (never in a normal demo env).
            try:
                package = agent.handoff_summary()
            except Exception as exc:  # noqa: BLE001 — never traceback mid-demo
                print(f"Agent error: {exc}", file=output)
                continue
            if package is None:
                print(
                    "Handoff summary is not available yet — the eligibility "
                    "screen must be completed first.",
                    file=output,
                )
            else:
                print(package["summary"], file=output)
                print(f"[{package['banner']}]", file=output)
            output.flush()
            continue
        if not user_message.strip():
            # An accidental extra Enter must NOT end the session — in a
            # live demo that would kill the conversation mid-flow. Exit
            # is explicit ("exit"/"quit"), Ctrl-C, or EOF.
            continue
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
    agent = FoodPathAgent(llm_client=client)
    try:
        return _run_repl(agent)
    except KeyboardInterrupt:
        print("Goodbye.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
