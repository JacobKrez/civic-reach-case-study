"""W70 regression: normalize_income must prefer a number adjacent to an
income cue over a merely-earlier number in the text.

Discovered while pinning W67 (see test_terse_multi_field_income.py):
``normalize_income("500 household members, income is 300")`` returned
500.0 instead of 300.0. ``_extract_money_number`` (normalize_income's
plain-monthly-figure branch) just returned the FIRST money-shaped number
in the text, with no awareness of which number was actually adjacent to
an income cue. This is exploitable precisely because W61/W67's
``_has_role_bearing_number`` gate (in src/agent.py) lets a multi-number
reply through to normalize_income exactly when it recognizes a
role-bearing number -- but until this fix, normalize_income itself threw
that role-bearing information away and fell back to raw position.

The fix adds ``_INCOME_ADJACENT_MONEY_PATTERN``: when a message has a
number directly adjacent to an unambiguous income word ("income",
"make(s)", "earn(s/ed)", "salary/salaries", "wages"), that number wins
over an earlier, unrelated one. "pay"/"paid"/"pays" is deliberately
EXCLUDED from this pattern -- it's ambiguous between earning ("they pay
me 1800") and an expense ("I pay 400 for daycare"), and preferring it
would risk grabbing a deduction amount instead of income, the same
wrong-verdict bug class this fix closes.
"""

from __future__ import annotations

import pytest

from src.guardrails.ambiguity import normalize_income


@pytest.mark.parametrize(
    "text,expected",
    [
        ("500 household members, income is 300", 300.0),
        ("2,000 people and my income is 1,800", 1800.0),
        ("1000 people, my income is 2500", 2500.0),
        ("2000 people and I make 500", 500.0),
        ("3 kids and I earn 1200", 1200.0),
        ("household of 4, salary is 2200", 2200.0),
        ("2 people, wages are 1900", 1900.0),
    ],
)
def test_w70_income_adjacent_number_wins_over_earlier_number(text, expected):
    result = normalize_income(text)
    assert result == pytest.approx(expected, abs=0.01), (
        f"W70: normalize_income({text!r}) should prefer the income-adjacent "
        f"number ({expected}), not an earlier unrelated one; got {result!r}"
    )


def test_w70_does_not_regress_single_number_replies():
    """When there's only one number in the message, behavior must be
    unchanged (this fix only changes SELECTION among multiple numbers).
    """
    assert normalize_income("$2,500") == pytest.approx(2500.0, abs=0.01)
    assert normalize_income("about $1,800") == pytest.approx(1800.0, abs=0.01)
    assert normalize_income("I make $2,100/month before taxes") == pytest.approx(
        2100.0, abs=0.01
    )


def test_w70_full_four_digit_number_is_not_truncated():
    """Regression within a regression: the income-adjacent capture group
    must read a plain (non comma-grouped) 4+ digit number whole, not
    truncate it the way \\d{1,3}(?:,\\d{3})* alone would (e.g. "1800"
    truncating to "180"). Caught while implementing this fix.
    """
    result = normalize_income("I pay 400 for daycare, and my income is 1800")
    assert result == pytest.approx(1800.0, abs=0.01), (
        f"W70: the full income figure (1800) must not be truncated to "
        f"180 by an under-scoped digit-capture group; got {result!r}"
    )


def test_w70_pay_word_is_not_treated_as_an_income_cue():
    """"pay" is deliberately excluded from the income-adjacent pattern:
    it's ambiguous between earning and an expense. This test pins that
    exclusion is intentional -- if this starts failing because "pay" was
    added back to the pattern, that's a deliberate, reviewed decision,
    not an accident.
    """
    from src.guardrails.ambiguity import _INCOME_ADJACENT_MONEY_PATTERN

    assert _INCOME_ADJACENT_MONEY_PATTERN.search("I pay 400 for daycare") is None


def test_w70_unstructured_multi_number_still_declines_via_agent_gate():
    """W70 changes SELECTION inside normalize_income; it must not weaken
    the upstream W61/W67 decline-to-guess gate in src/agent.py for
    genuinely unstructured multi-number replies (no role-bearing number
    at all). "27, 0, and 100" has no income cue anywhere, so the agent's
    _fallback_parse_terse_reply must still decline -- normalize_income is
    never even reached for this case via that path.
    """
    from src.agent import FoodPathAgent

    class _SilentLLM:
        def chat(self, messages, system=None, tools=None):
            if isinstance(system, str) and "intent classifier" in system.lower():
                return {"content": "normal", "tool_calls": []}
            return {"content": "", "tool_calls": []}

    agent = FoodPathAgent(llm_client=_SilentLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")
    agent.turn("27, 0, and 100")
    assert agent.state.gross_monthly_income is None, (
        "W70 must not weaken the W61/W67 unstructured-multi-number "
        f"decline-to-guess gate; got {agent.state.gross_monthly_income!r}"
    )
