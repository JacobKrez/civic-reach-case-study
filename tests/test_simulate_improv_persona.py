"""W71: the LLM-driven improv persona (scripts/simulate.py).

Offline/mocked coverage only -- the persona's whole point is to call a
live LLM per turn (see the module docstring in scripts/simulate.py), so
these tests stub src.llm_client.LLMClient rather than hitting a real
provider. Live behavior was verified manually against MiniMax during
development (natural multi-fact/out-of-order/self-correcting replies,
and it surfaced a real bug -- see tests/test_ambiguity.py's
hundred_shorthand_* cases, found via this exact persona).
"""

from __future__ import annotations

import random

import pytest

import scripts.simulate as sim


class _StubLLMClient:
    """Deterministic stand-in for src.llm_client.LLMClient."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self._replies = list(replies) if replies is not None else ["ok"]
        self._calls: list[dict] = []

    def chat(self, messages, system=None, tools=None):
        self._calls.append({"messages": messages, "system": system})
        text = self._replies.pop(0) if self._replies else ""
        return {"content": text, "tool_calls": []}


class _RaisingLLMClient:
    def chat(self, messages, system=None, tools=None):
        raise RuntimeError("simulated provider outage")


def test_improv_persona_is_registered():
    assert "improv" in sim.PERSONAS
    assert sim.PERSONAS["improv"].reply is sim._improv_reply


def test_improv_reply_returns_stubbed_text(monkeypatch):
    monkeypatch.setattr(
        "src.llm_client.LLMClient", lambda: _StubLLMClient(["there are 3 of us"])
    )
    scenario = sim.SCENARIOS[0]
    state = sim.ConversationState()
    rng = random.Random(1)
    reply = sim._improv_reply("", scenario, rng, state)
    assert reply == "there are 3 of us"


def test_improv_reply_tracks_message_history(monkeypatch):
    monkeypatch.setattr(
        "src.llm_client.LLMClient",
        lambda: _StubLLMClient(["hi there", "3 people"]),
    )
    scenario = sim.SCENARIOS[0]
    state = sim.ConversationState()
    rng = random.Random(2)
    sim._improv_reply("", scenario, rng, state)
    sim._improv_reply("What is your household size?", scenario, rng, state)
    # Two turns -> 4 entries (user+assistant per turn) in the persona's
    # own running history.
    assert len(state.improv_messages) == 4
    assert state.improv_messages[0]["role"] == "user"
    assert state.improv_messages[1]["role"] == "assistant"
    assert state.improv_messages[1]["content"] == "hi there"


def test_improv_reply_samples_flavors_once_and_keeps_them(monkeypatch):
    monkeypatch.setattr(
        "src.llm_client.LLMClient", lambda: _StubLLMClient(["a", "b", "c"])
    )
    scenario = sim.SCENARIOS[0]
    state = sim.ConversationState()
    rng = random.Random(3)
    sim._improv_reply("", scenario, rng, state)
    first_flavors = list(state.improv_flavors)
    assert first_flavors  # non-empty: flavors were sampled
    sim._improv_reply("agent said something", scenario, rng, state)
    # Same flavors across turns -- sampled once, not re-rolled per turn.
    assert state.improv_flavors == first_flavors


def test_improv_reply_ends_after_turn_budget(monkeypatch):
    monkeypatch.setattr(
        "src.llm_client.LLMClient", lambda: _StubLLMClient(["keep going"] * 20)
    )
    scenario = sim.SCENARIOS[0]
    state = sim.ConversationState()
    rng = random.Random(4)
    replies = []
    for _ in range(15):
        r = sim._improv_reply("agent reply", scenario, rng, state)
        replies.append(r)
        if r == "":
            break
    assert replies[-1] == ""
    assert len(replies) <= 10  # budget is 9 turns, +1 for the empty terminator


def test_improv_reply_reacts_to_a_verdict_without_calling_the_llm(monkeypatch):
    """Once the agent's reply looks like a verdict, the persona gives a
    short canned reaction locally rather than spending another live call
    on it (mirrors _run_one's own completion check)."""
    stub = _StubLLMClient(["should never be used"])
    monkeypatch.setattr("src.llm_client.LLMClient", lambda: stub)
    scenario = sim.SCENARIOS[0]
    state = sim.ConversationState()
    state.turn_index = 3
    rng = random.Random(5)
    reply = sim._improv_reply(
        "Screening result: likely eligible. ...", scenario, rng, state
    )
    assert reply
    assert not stub._calls, "verdict-reaction turn must not call the LLM"


def test_improv_reply_survives_a_provider_error(monkeypatch):
    """A provider hiccup during the persona's own call must end the
    script cleanly (empty string), not raise out of persona.reply() and
    get misfiled as an agent-side no_traceback finding."""
    monkeypatch.setattr("src.llm_client.LLMClient", _RaisingLLMClient)
    scenario = sim.SCENARIOS[0]
    state = sim.ConversationState()
    rng = random.Random(6)
    reply = sim._improv_reply("agent reply", scenario, rng, state)
    assert reply == ""


def test_improv_reply_empty_llm_response_ends_conversation(monkeypatch):
    monkeypatch.setattr("src.llm_client.LLMClient", lambda: _StubLLMClient([""]))
    scenario = sim.SCENARIOS[0]
    state = sim.ConversationState()
    rng = random.Random(7)
    reply = sim._improv_reply("agent reply", scenario, rng, state)
    assert reply == ""


@pytest.mark.parametrize("scenario", sim.SCENARIOS, ids=lambda s: s.sid)
def test_improv_system_prompt_states_every_scenario_fact(monkeypatch, scenario):
    """The persona's system prompt must always contain the scenario's
    true facts (in whatever units the prompt uses) so the model has a
    ground truth to stay faithful to -- not just for scenarios with
    deductions, but every scenario the daemon will actually sample."""
    captured: dict = {}

    class _CapturingClient:
        def chat(self, messages, system=None, tools=None):
            captured["system"] = system
            return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr("src.llm_client.LLMClient", _CapturingClient)
    state = sim.ConversationState()
    rng = random.Random(8)
    sim._improv_reply("", scenario, rng, state)
    prompt = captured["system"]
    assert str(scenario.household_size) in prompt
    assert str(int(scenario.gross_monthly_income)) in prompt
    assert ("yes" if scenario.has_elderly_or_disabled else "no") in prompt.lower()
