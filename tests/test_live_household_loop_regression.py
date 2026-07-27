"""Regression for the live household loop reported on 2026-07-26."""

from src.agent import FoodPathAgent
from src.calculator import income_limits
from src.web import PAGE


class SilentProvider:
    """Force every important transition through deterministic backstops."""

    def chat(self, *_args, **_kwargs):
        return {"content": "", "tool_calls": []}


def test_reported_large_household_transcript_moves_forward(monkeypatch):
    """Originally filed against a 27-person household. household_size is
    capped at 20 (``calculator.max_household_size()`` — the calculator's
    own precomputed-table boundary; the source SNAP documents state no
    household-size ceiling at all, only a per-additional-person formula),
    so this regression now uses 17 -- still large enough to exercise the
    extended additional-person table row, but a valid intake value."""
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    agent = FoodPathAgent(llm_client=SilentProvider())

    hello = agent.turn("hello")
    snap = agent.turn("what is snap")
    estimate = agent.turn("do i get it?")
    clarification = agent.turn("0, i dont own a house")
    household = agent.turn("17")
    income = agent.turn("0")
    verdict = agent.turn("yes")

    assert "automated" not in hello.lower()
    assert "helps households" in snap
    assert "three short details" in estimate
    assert "do not need to own a home" in clarification

    assert agent.state.household_size == 17
    assert agent.state.gross_monthly_income == 0
    assert agent.state.has_elderly_or_disabled_member is True
    assert "17 people" in household
    assert "gross monthly income" in household
    assert "$0" in income
    assert "elderly" in income
    assert "likely eligible" in verdict.lower()

    # The FY2026 additional-person amounts continue past the published 1-8
    # table; a large valid household never falls back into the intake loop.
    assert income_limits(17)["net"] == 4513 + (17 - 8) * 459


def test_initial_intro_is_single_and_inside_conversation_log():
    intro_markup = '<div class="bubble assistant" data-role="intro">'
    assert PAGE.count(intro_markup) == 1
    assert 'data-role="disclosure"' not in PAGE
    assert 'data-role="greeting"' not in PAGE
    assert PAGE.index('id="messages"') < PAGE.index('data-role="intro"') < PAGE.index('id="form"')
