"""W71b regression: a Spanish verdict reply must never mix in the
calculator's raw English notes.

Live human review, 2026-07-28 (found via the simulator's new improv
persona and the faster daemon cadence surfacing a long-standing,
intermittent finding more often). ``_outcome_label_floor``'s fallback
path (fires when the model's own reply is empty or does not contain a
recognized outcome term) concatenated a correctly-Spanish outcome label
with the calculator's ``notes`` field -- which is English-only prose,
since ``calculate_eligibility`` has no ``language`` parameter and its
docstring commits to byte-identical output for existing callers.
Reproduced live: "Resultado de evaluación: probablemente califica.
Household has no elderly (60+) or disabled member and is screened
under North Carolina's Broad-Based Categorical Eligibility (BBCE)..."
-- a Spanish label glued to an English explanation sentence.

The fix adds ``_outcome_notes_es``, a short, fully-Spanish summary built
from the result dict's already-computed, language-neutral numeric
fields (income_considered, limit, household_size, test_used), used only
when language == "es". It carries less detail than the English notes
(no deduction breakdown, no boundary note) but is never mixed-language.
"""

from __future__ import annotations

import pytest

from src.agent import _outcome_label_floor, _outcome_notes_es


_RESULT_GROSS_ELIGIBLE = {
    "qualifies": True,
    "test_used": "gross",
    "limit": 5360.0,
    "expanded_limit": 5360.0,
    "bbce_limit": 5360.0,
    "income_considered": 3200.0,
    "household_size": 6,
    "outcome": "likely_eligible",
    "deductions": None,
    "notes": (
        "Household has no elderly (60+) or disabled member and is "
        "screened under North Carolina's Broad-Based Categorical "
        "Eligibility (BBCE). Under NC BBCE the gross income screen is "
        "200% of the federal poverty guideline (NCDHHS); the federal "
        "130% floor applies in other states. Monthly income $3,200.00 "
        "vs gross limit $5,360.00 for household of 6. Result: likely "
        "qualifies. The household passes the initial applicable income "
        "test."
    ),
}

_RESULT_NET_INELIGIBLE = {
    "qualifies": False,
    "test_used": "net",
    "limit": 1000.0,
    "expanded_limit": 2000.0,
    "bbce_limit": 2000.0,
    "income_considered": 1500.0,
    "household_size": 2,
    "outcome": "likely_ineligible",
    "deductions": None,
    "notes": "Some English-only explanation for a net-test ineligible household.",
}


def test_w71b_spanish_fallback_reply_contains_no_english_notes_fragments():
    """The exact live-bug fragments must never appear when the
    deterministic floor renders a Spanish reply."""
    reply = _outcome_label_floor("", _RESULT_GROSS_ELIGIBLE, "es")
    assert "Household has no elderly" not in reply
    assert "is screened under" not in reply
    assert "Broad-Based Categorical Eligibility" not in reply
    assert "passes the initial applicable income test" not in reply


def test_w71b_spanish_fallback_reply_is_fully_spanish_outcome_label():
    reply = _outcome_label_floor("", _RESULT_GROSS_ELIGIBLE, "es")
    assert reply.startswith("Resultado de evaluación: probablemente califica.")


def test_w71b_spanish_fallback_reply_still_carries_the_key_numbers():
    """Losing the English notes' extra detail is an acceptable
    tradeoff, but the core numbers (income, limit, household size)
    that justify the verdict must still be present."""
    reply = _outcome_label_floor("", _RESULT_GROSS_ELIGIBLE, "es")
    assert "3,200" in reply
    assert "5,360" in reply
    assert "6" in reply


def test_w71b_english_fallback_reply_is_unchanged():
    """Non-regression: the English fallback path must still use the
    calculator's own (English) notes verbatim -- this fix must not
    touch English behavior at all."""
    reply = _outcome_label_floor("", _RESULT_GROSS_ELIGIBLE, "en")
    assert "Household has no elderly (60+) or disabled member" in reply
    assert reply.startswith("Screening result: likely eligible.")


def test_w71b_a_good_model_reply_is_left_alone_in_both_languages():
    """The floor is a fallback ONLY -- when the model's own reply
    already contains a recognized outcome term, it must pass through
    unchanged, in either language."""
    good_en = "You are likely eligible for FNS."
    good_es = "Probablemente califica para FNS."
    assert _outcome_label_floor(good_en, _RESULT_GROSS_ELIGIBLE, "en") == good_en
    assert _outcome_label_floor(good_es, _RESULT_GROSS_ELIGIBLE, "es") == good_es


@pytest.mark.parametrize(
    "test_used,expected_label",
    [
        ("gross", "bruto"),
        ("net", "neto"),
        ("expanded", "bruto ampliado"),
    ],
)
def test_w71b_outcome_notes_es_translates_test_used(test_used, expected_label):
    result = dict(_RESULT_GROSS_ELIGIBLE, test_used=test_used)
    notes = _outcome_notes_es(result)
    assert expected_label in notes


def test_w71b_outcome_notes_es_handles_ineligible_net_case():
    reply = _outcome_label_floor("", _RESULT_NET_INELIGIBLE, "es")
    assert "English-only" not in reply
    assert "neto" in reply
    assert "1,500" in reply
    assert "1,000" in reply
    assert reply.startswith("Resultado de evaluación: probablemente no califica")


def test_w71b_outcome_notes_es_missing_fields_degrades_to_empty_string():
    """A malformed/partial result dict must not raise -- degrade to an
    empty notes string rather than crashing the reply."""
    assert _outcome_notes_es({}) == ""
    assert _outcome_notes_es({"income_considered": 100.0}) == ""
