r"""W68 regression: a comma-formatted household-size number must not be
truncated at the comma.

Live human review, 2026-07-28. The deterministic fallback's household_size
branch matched a leading number with ``re.match(r"^\$?(\d{1,4})\b", text)``.
Since "," is not a digit, "1,000 people" matched only the "1" before the
comma -- and 1 is a syntactically valid household size (1 <= 1 <=
max_household_size()), so the fallback returned 1 immediately and never
even looked at the rest of the message. The actually-stated number
(1,000, itself absurd and correctly out of range) was silently discarded
in favor of a truncation artifact. Reproduced live via
``_fallback_parse_terse_reply`` directly: "1,000 people, and then 0 montly
income" recorded household_size=1.

The same truncation shape would also misfire on a legitimate value: a
household of, say, 5 typed with an accidental comma-thousands separator
("1,005 people" -- a typo, but the trailing-number branch would read
"005" as 5, a plausible-looking value that would have silently landed).

The fix widens every digit-capture group in the household_size branch of
``_fallback_parse_terse_reply`` from ``\d{1,4}`` to ``\d{1,3}(?:,\d{3})*``
(stripping commas before ``int()``), so a comma-grouped number is always
read as one number and range-checked as a whole -- never truncated to its
first digit group.
"""

from __future__ import annotations

import os

os.environ.setdefault("GUARDRAIL_CLASSIFIER", "off")

import pytest

from src.agent import FoodPathAgent


class _NoToolCallLLM:
    """Bare empty tool-call stub, forcing the deterministic fallback to
    handle every reply directly -- the path that actually misfired in the
    2026-07-27/28 human review.
    """

    def chat(self, messages, system=None, tools=None):
        if isinstance(system, str) and "intent classifier" in system.lower():
            return {"content": "normal", "tool_calls": []}
        return {"content": "", "tool_calls": []}


def test_w68_comma_thousands_household_size_declines_instead_of_truncating():
    """"1,000 people" must not resolve to household_size=1.

    1,000 is an absurd household size and should be declined (None) --
    exactly like the un-comma-formatted "1000 people" already was before
    this fix -- not silently truncated to the leading "1".
    """
    agent = FoodPathAgent(llm_client=_NoToolCallLLM())
    result = agent._fallback_parse_terse_reply("household_size", "1,000 people")
    assert result is None, (
        "W68: '1,000 people' must decline (None), not truncate to the "
        f"leading digit; got {result!r}"
    )


def test_w68_exact_live_bug_reply_declines_household_size():
    """The exact live-review reply must not set household_size=1."""
    agent = FoodPathAgent(llm_client=_NoToolCallLLM())
    result = agent._fallback_parse_terse_reply(
        "household_size", "1,000 people, and then 0 montly income"
    )
    assert result is None, (
        "W68: the exact live-review reply must not silently resolve to "
        f"household_size=1 via comma truncation; got {result!r}"
    )


def test_w68_comma_number_does_not_reach_agent_state():
    """End-to-end: driving a real turn with the bug reply must leave
    household_size unset rather than recording 1.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallLLM())
    agent.turn("1,000 people, and then 0 montly income")
    assert agent.state.household_size is None, (
        "W68: household_size must remain unset after a comma-truncated "
        f"reply, not silently become 1; got {agent.state.household_size!r}"
    )


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("1,000 people", None),          # out of range once read whole
        ("1,000 people sometimes 2,000", None),
        ("21,000", None),
        ("3", 3),                         # unambiguous single-digit form unaffected
        ("20", 20),                       # exactly at the max, still works
        ("household of 4", 4),            # phrase form unaffected
        ("there are 6 of us", 6),         # phrase form unaffected
        ("5 people in our household", 5), # trailing form unaffected
    ],
)
def test_w68_parametrized_household_size_shapes(reply, expected):
    agent = FoodPathAgent(llm_client=_NoToolCallLLM())
    result = agent._fallback_parse_terse_reply("household_size", reply)
    assert result == expected, (
        f"W68: {reply!r} expected household_size={expected!r}, got {result!r}"
    )


def test_w68_comma_trailing_digit_group_does_not_masquerade_as_a_small_value():
    """A comma-grouped number whose trailing 3-digit chunk happens to fall
    inside the valid 1..max_household_size() range must not be picked up
    by the trailing-number branch as if it were a bare small number.

    "1,005 people" must decline (1005 is out of range) rather than
    reading the trailing "005" as 5.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallLLM())
    result = agent._fallback_parse_terse_reply("household_size", "1,005 people")
    assert result is None, (
        "W68: '1,005 people' must decline as a whole (1005 is out of "
        f"range), not read the trailing '005' as household_size=5; got "
        f"{result!r}"
    )
