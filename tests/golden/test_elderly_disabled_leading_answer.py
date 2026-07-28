"""A leading yes/no answers the elderly/disabled question, whatever follows.

Found in live human review on 2026-07-28. The has_elderly_or_disabled_member
fallback required the ENTIRE reply to be a single bare word ("yes", "no",
"nope"...). Any trailing content at all -- "yes all 150", "yes all of
them", "yeah definitely" -- made the field silently fail to parse, and it
kept failing across three consecutive tries in the live transcript, each
with different trailing content, before a bare "yes" finally landed. Per
explicit instruction: this is a general pattern (ANY trailing content),
not something to special-case for one number or phrase.

The fix reads the leading word as the answer and treats everything after
it as emphasis or (confused) elaboration -- except when the trailing
content itself carries an income cue ("yes, $1,800"), which usually means
the user is affirming a DIFFERENT field's value, not answering this one.
Guessing across fields is exactly the class of bug this screen cannot
afford (see the W61 income-misparse fixture); the guard keeps this fix
from reintroducing that shape of bug one field over.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent


class SilentProvider:
    """Force every transition through the deterministic backstop."""

    def chat(self, *_args, **_kwargs):
        return {"content": "", "tool_calls": []}


def _parse(text: str):
    return FoodPathAgent._fallback_parse_terse_reply(
        None, "has_elderly_or_disabled_member", text
    )


# ---------------------------------------------------------------------------
# 1. A leading yes/no is extracted regardless of what trails it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes all 150", True),
        ("yes all of them", True),
        ("yeah definitely", True),
        ("yeah 42", True),
        ("yes 3", True),
        ("yes, definitely, my grandmother", True),
        ("no one", False),
        ("nope none of them", False),
        ("no 0", False),
        ("nah none", False),
    ],
)
def test_leading_yes_no_is_extracted_regardless_of_trailing_content(text, expected):
    assert _parse(text) is expected


# ---------------------------------------------------------------------------
# 2. Not a blank check -- genuinely unclear replies still return None.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["maybe", "depends", "what does that mean", ""])
def test_genuinely_ambiguous_replies_are_still_left_unparsed(text):
    assert _parse(text) is None


# ---------------------------------------------------------------------------
# 3. Regression guard: a "yes"/"no" attached to an income cue is affirming
#    a DIFFERENT field, not answering this one -- never guess across fields.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "yes, $1,800",
        "no, my income is 2000",
        "yes that's right, $2,500 a month",
    ],
)
def test_leading_yes_no_with_an_income_cue_is_not_captured(text):
    """Caught in review before shipping: this exact case broke
    test_same_value_restated_is_not_a_correction, where a user
    re-stating "yes, $1,800" (affirming their income figure, asked
    about elderly/disabled next because income and household were
    already filled) was wrongly recorded as elderly=True."""
    assert _parse(text) is None


# ---------------------------------------------------------------------------
# 4. End to end: the exact live-transcript pattern (three consecutive
#    "yes + trailing content" replies) all resolve on the first try now.
# ---------------------------------------------------------------------------


def test_three_consecutive_yes_with_trailing_content_all_resolve_on_first_try():
    agent = FoodPathAgent(llm_client=SilentProvider())
    agent.turn("1")
    agent.turn("0")
    reply = agent.turn("yes all 150")
    assert agent.state.has_elderly_or_disabled_member is True
    assert "does anyone" not in reply.lower(), (
        f"the field parsed but the reply still re-asked it: {reply!r}"
    )


def test_restating_income_after_it_moves_to_elderly_question_is_not_a_correction():
    """The regression this file exists to prevent, at the agent level:
    restating income with a leading "yes" must not silently set
    elderly=True and then have the real answer register as a
    correction rather than a first-time fill."""
    agent = FoodPathAgent(llm_client=SilentProvider())
    agent.turn("3")
    agent.turn("1800")
    agent.turn("yes, $1,800")  # re-affirms income, does not answer elderly
    assert agent.state.has_elderly_or_disabled_member is None
    agent.turn("no")
    assert agent.state.has_elderly_or_disabled_member is False
