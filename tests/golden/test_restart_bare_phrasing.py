"""A bare "start over" must actually reset state.

Live human review, 2026-07-28. Every existing _RESTART_PATTERNS entry
required a leading qualifier ("can we...", "let's...", "actually...",
"I want to...", "never mind..."). A bare, standalone "start over" -- the
single most natural phrasing a real user would type -- matched none of
them. Reproduced against the real live provider: 0 of 5 runs actually
reset state; the request fell through to the main LLM, which handled it
inconsistently in free-form prose and never once cleared the intake, with
reply text that sometimes explicitly claimed to have restarted while
leaving household_size untouched.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent
from src.guardrails.restarter import detect_restart_request


class SilentProvider:
    def chat(self, *_args, **_kwargs):
        return {"content": "", "tool_calls": []}


# ---------------------------------------------------------------------------
# 1. The detector itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "start over",
        "Start over",
        "START OVER",
        "restart",
        "restart.",
        "restart!",
        "start again",
        "begin again",
        "please start over",
        "restart please",
        "redo this",
        "redo that",
        "redo the screening",
    ],
)
def test_bare_restart_phrasing_is_detected(text):
    assert detect_restart_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # Incidental mentions must not fire -- the module's own stated
        # principle: a restart firing by accident destroys real data,
        # which is worse than a restart that doesn't fire.
        "the process starts over each month",
        "I will restart my job search next week",
        "wait, I have another question",
        "actually I need to fix something",
        "there are 3 people in my household",
        "yes, restart my application later",
        "",
        "   ",
    ],
)
def test_incidental_mentions_do_not_false_positive(text):
    assert detect_restart_request(text) is False


# ---------------------------------------------------------------------------
# 2. End to end: state actually clears.
# ---------------------------------------------------------------------------


def test_bare_start_over_actually_clears_household_size():
    agent = FoodPathAgent(llm_client=SilentProvider())
    agent.turn("3")
    assert agent.state.household_size == 3
    agent.turn("start over")
    assert agent.state.household_size is None


def test_bare_start_over_clears_all_three_intake_fields():
    agent = FoodPathAgent(llm_client=SilentProvider())
    agent.turn("3")
    agent.turn("2200")
    agent.turn("no")
    assert agent.eligibility_result is not None
    agent.turn("restart")
    assert agent.state.household_size is None
    assert agent.state.gross_monthly_income is None
    assert agent.state.has_elderly_or_disabled_member is None
    assert agent.eligibility_result is None
