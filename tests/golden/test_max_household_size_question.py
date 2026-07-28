"""W69 regression: "what's the max household size?" must not be
improvised.

Live human review, 2026-07-28. Asked whether there's a maximum household
size, the model answered "no max... tops out around 8 to 10 people" -- a
fabricated figure. The real answer is grounded in
``calculator.max_household_size()`` (20), which is this screener's own
operational form limit -- SNAP itself has no statutory maximum household
size. The fix adds a deterministic handler (mirroring the existing
benefit-amount hallucination guard) so this question always gets the
real, code-sourced number instead of a model-improvised one.
"""

from __future__ import annotations

import os

os.environ.setdefault("GUARDRAIL_CLASSIFIER", "off")

from src.agent import FoodPathAgent
from src.calculator import max_household_size
from src.guardrails.household_size_question import (
    detect_max_household_size_question,
    max_household_size_response,
)

import pytest


class _SilentLLM:
    def chat(self, messages, system=None, tools=None):
        if isinstance(system, str) and "intent classifier" in system.lower():
            return {"content": "normal", "tool_calls": []}
        return {"content": "", "tool_calls": []}


@pytest.mark.parametrize(
    "text",
    [
        "what is the max household size?",
        "What's the maximum household size?",
        "is there a maximum household size",
        "is there a limit on household size",
        "does household size have a cap",
        "how many people can be in my household",
        "how many people can I put in my household",
        "what's the biggest household you can screen",
    ],
)
def test_w69_detects_max_household_size_question(text):
    assert detect_max_household_size_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "there are 3 people in my household",
        "What is SNAP?",
        "how much will I get in food stamps",
        "",
        "   ",
        "my household income is 2000",
    ],
)
def test_w69_does_not_false_positive(text):
    assert detect_max_household_size_question(text) is False


def test_w69_response_cites_the_real_calculator_cap():
    """The response must contain the ACTUAL max_household_size() figure,
    sourced from the calculator -- never a hardcoded/hallucinated number.
    """
    cap = max_household_size()
    response = max_household_size_response("en")
    assert str(cap) in response
    # The exact fabricated live-bug figures must never appear as the
    # stated cap.
    assert "8 to 10" not in response
    assert "tops out" not in response.lower()


def test_w69_response_is_grounded_not_hallucinated_end_to_end():
    """Driving a real turn must return the code-grounded answer, not
    reach the model's own free-text generation at all (the SilentLLM
    provider returns nothing useful, so if this reached the model the
    reply would be empty/generic instead of citing 20).
    """
    agent = FoodPathAgent(llm_client=_SilentLLM())
    reply = agent.turn("what is the max household size?")
    assert str(max_household_size()) in reply
    assert "8 to 10" not in reply
    assert "tops out" not in reply.lower()


def test_w69_folds_the_next_pending_question():
    """The deterministic answer must not derail intake -- the next
    missing field's question is folded onto the reply so the user has
    something concrete to answer next.
    """
    agent = FoodPathAgent(llm_client=_SilentLLM())
    reply = agent.turn("is there a maximum household size")
    assert "household" in reply.lower()
    # missing_fields()[0] is household_size on a fresh agent -- the
    # canonical next-field question should be present in the reply.
    assert agent.state.missing_fields()[0] == "household_size"


def test_w69_spanish_response_also_grounded():
    response = max_household_size_response("es")
    cap = max_household_size()
    assert str(cap) in response
