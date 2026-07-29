"""The verdict a resident reads must be plain language.

The continuous simulator's judge flagged ``verdict_plain_language`` as the
single most frequent quality failure (121 occurrences in one overnight
run). It was right. What a resident saw was:

    Screening result: likely eligible. Household has no elderly (60+) or
    disabled member and is screened under North Carolina's Broad-Based
    Categorical Eligibility (BBCE). Under NC BBCE the gross income screen
    is 200% of the federal poverty guideline (NCDHHS); the federal 130%
    floor applies in other states. Monthly income $1,600.00 vs gross
    limit $4,442.00 for household of 3. Result: likely qualifies. The
    household passes the initial applicable income test.
    (Sources: calculator.income_limits(), 02_income_limits.md.)

It states the verdict three times, speaks about the user in the third
person, spends most of its words on a rule that is not theirs to care
about, and cites a Python function signature. The system prompt targets a
6th-grade reading level.

The calculator now returns both: ``notes`` (unchanged — the technical
record the audit trail and calculator tests read) and ``plain_notes``,
which is what the reply renders.
"""

from __future__ import annotations

import re

import pytest

from src.calculator import calculate_eligibility

# Terms that mean nothing to someone asking whether they can get help
# buying food, and which must not appear in the text addressed to them.
JARGON = [
    "broad-based categorical",
    "bbce",
    "ncdhhs",
    "poverty guideline",
    "gross income test",
    "net income test",
    "income_limits()",
    "calculator.",
    "state_code",
]

CASES = [
    (3, 1600.0, False),
    (1, 900.0, False),
    (4, 10000.0, False),
    (2, 900.0, True),
    (1, 1800.0, False),
    (6, 3000.0, True),
    (20, 5000.0, False),
]


@pytest.mark.parametrize("household,income,elderly", CASES)
def test_plain_notes_contain_no_program_jargon(household, income, elderly):
    result = calculate_eligibility(household, income, elderly)
    text = result["plain_notes"].lower()
    for term in JARGON:
        assert term not in text, f"jargon {term!r} in: {result['plain_notes']!r}"


@pytest.mark.parametrize("household,income,elderly", CASES)
def test_plain_notes_address_the_user_directly(household, income, elderly):
    """Second person, not "the household"."""
    result = calculate_eligibility(household, income, elderly)
    text = result["plain_notes"].lower()
    assert "your" in text or "you" in text, result["plain_notes"]


@pytest.mark.parametrize("household,income,elderly", CASES)
def test_plain_notes_state_the_outcome_exactly_once(household, income, elderly):
    """The old text said it three times ("likely eligible", "Result:
    likely qualifies", "passes the initial applicable income test")."""
    result = calculate_eligibility(household, income, elderly)
    text = result["plain_notes"].lower()
    hits = len(re.findall(r"likely (?:qualif|do not qualif)", text))
    assert hits <= 1, f"outcome stated {hits} times: {result['plain_notes']!r}"


@pytest.mark.parametrize("household,income,elderly", CASES)
def test_plain_notes_still_show_the_numbers_that_justify_the_verdict(
    household, income, elderly
):
    """Plain language must not mean vague. The user's income and the
    limit it was compared against both have to be visible."""
    result = calculate_eligibility(household, income, elderly)
    text = result["plain_notes"]
    assert f"{result['income_considered']:,.2f}" in text, text
    assert f"{result['limit']:,.2f}" in text, text


@pytest.mark.parametrize("household,income,elderly", CASES)
def test_technical_notes_are_preserved_for_the_audit_trail(
    household, income, elderly
):
    """``plain_notes`` is an addition, not a replacement. The precise
    record must survive for telemetry, the handoff summary, and review."""
    result = calculate_eligibility(household, income, elderly)
    assert result["notes"]
    assert result["notes"] != result["plain_notes"]


def test_a_no_answer_to_the_elderly_question_is_acknowledged():
    """The user answers that question immediately before the verdict; a
    verdict that never mentions it reads as though the answer went
    nowhere."""
    result = calculate_eligibility(3, 1600.0, False)
    text = result["plain_notes"].lower()
    assert "60 or older" in text or "disability" in text, result["plain_notes"]


def test_an_elderly_household_is_told_deductions_were_considered():
    result = calculate_eligibility(2, 900.0, True)
    text = result["plain_notes"].lower()
    assert "60 or older" in text or "disability" in text, result["plain_notes"]


def test_an_over_limit_household_is_told_what_could_still_change_it():
    """A borderline household must not be left thinking the door is shut."""
    result = calculate_eligibility(1, 1800.0, False)
    assert result["outcome"] == "uncertain_apply_anyway"
    text = result["plain_notes"].lower()
    assert "may still qualify" in text, result["plain_notes"]
    assert "applying" in text or "apply" in text, result["plain_notes"]


def test_the_user_facing_citation_names_no_code_symbol():
    """The citation line must name a source a person can look up."""
    from src.agent import FoodPathAgent

    class _Stub:
        def chat(self, messages, system=None, tools=None):
            return {"content": "", "tool_calls": []}

    agent = FoodPathAgent(llm_client=_Stub(), retriever=lambda _q: [])
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 1600.0
    agent.state.has_elderly_or_disabled_member = False
    agent._calculate_once()
    agent._context("")
    for citation in agent._prompt_citations:
        shown = citation.get("label") or citation.get("doc")
        assert "()" not in shown, shown
        assert "calculator." not in shown, shown
