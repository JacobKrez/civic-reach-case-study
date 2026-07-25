"""Tests for W17 case-worker handoff summary.

Covers the two spec'd areas:

1. handoff_summary() content + gating — never producible before intake
   is complete and a deterministic eligibility result exists; reflects
   real state only.
2. The agent-level package returned to the web UI / CLI is exactly
   ``{"summary", "banner"}`` — the dependency-free, local-only
   deliverable. There is no email layer, no SMTP, and no outbound
   contact path (W39 cut).
"""

from __future__ import annotations

import pytest

from src import handoff as handoff_mod
from src.agent import FoodPathAgent
from src.calculator import calculate_eligibility
from src.handoff import _HANDOFF_BANNER, handoff_summary
from src.state import AssessmentState


def _complete_state(**overrides) -> AssessmentState:
    values = {
        "household_size": 3,
        "gross_monthly_income": 2_000.0,
        "has_elderly_or_disabled_member": False,
    }
    values.update(overrides)
    return AssessmentState(**values)


def _result_for(state: AssessmentState) -> dict:
    assert state.household_size is not None
    assert state.gross_monthly_income is not None
    assert state.has_elderly_or_disabled_member is not None
    return calculate_eligibility(
        household_size=state.household_size,
        gross_monthly_income=state.gross_monthly_income,
        has_elderly_or_disabled=state.has_elderly_or_disabled_member,
    )


class _NoopLLM:
    """Never called by the handoff path; exists so the agent constructs."""

    def chat(self, messages, system=None, tools=None):  # pragma: no cover
        raise AssertionError("handoff path must not call the LLM")


# ---------------------------------------------------------------------
# 1. Gating — summary is never producible before intake is complete
# ---------------------------------------------------------------------

def test_handoff_summary_returns_none_when_intake_incomplete():
    state = AssessmentState()  # nothing collected
    assert handoff_summary(state, None) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"household_size": 3},
        {"household_size": 3, "gross_monthly_income": 2_000.0},
        {"gross_monthly_income": 2_000.0, "has_elderly_or_disabled_member": True},
    ],
)
def test_handoff_summary_returns_none_with_any_required_field_missing(kwargs):
    state = AssessmentState(**kwargs)
    # Even if a stale result were somehow present, incomplete intake gates it.
    assert handoff_summary(state, {"outcome": "likely_eligible"}) is None


def test_handoff_summary_returns_none_when_no_eligibility_result():
    state = _complete_state()
    assert handoff_summary(state, None) is None


def test_agent_handoff_summary_none_before_result_exists():
    agent = FoodPathAgent(llm_client=_NoopLLM())
    assert agent.handoff_summary() is None


def test_handoff_summary_never_calls_the_llm():
    agent = FoodPathAgent(llm_client=_NoopLLM())
    agent.state = _complete_state()
    agent._eligibility_result = _result_for(agent.state)
    package = agent.handoff_summary()
    assert package is not None  # would raise inside _NoopLLM.chat otherwise


# ---------------------------------------------------------------------
# 2. Content — reflects real state only, includes verdict + citations
# ---------------------------------------------------------------------

def test_handoff_summary_contains_intake_fields_and_verdict():
    state = _complete_state()
    result = _result_for(state)
    summary = handoff_summary(state, result)
    assert summary is not None
    assert "Household size:                    3" in summary
    assert "$2,000.00" in summary
    assert "No" in summary.split("Elderly (60+) or disabled member:")[1].splitlines()[0]
    # Deterministic verdict from the calculator, not invented text.
    expected_line = handoff_mod._OUTCOME_LINES[result["outcome"]]
    assert expected_line in summary
    assert "initial eligibility screen only, not an agency determination" in summary


def test_handoff_summary_includes_volunteered_deduction_fields_only():
    state = _complete_state(shelter_costs_monthly=850.0, medical_expenses_monthly=120.0)
    result = _result_for(state)
    summary = handoff_summary(state, result)
    assert summary is not None
    assert "VOLUNTEERED DEDUCTION DETAILS" in summary
    assert "Shelter costs (monthly): $850.00" in summary
    assert "Medical expenses (monthly): $120.00" in summary
    # Fields the user did NOT volunteer must not appear.
    assert "Dependent-care costs" not in summary
    assert "Earned (wage)" not in summary


def test_handoff_summary_omits_deduction_section_when_none_volunteered():
    state = _complete_state()
    summary = handoff_summary(state, _result_for(state))
    assert summary is not None
    assert "VOLUNTEERED DEDUCTION DETAILS" not in summary


def test_handoff_summary_lists_unique_citation_docs():
    state = _complete_state()
    citations = [
        {"doc": "knowledge_base/02_income_rules.md", "snippet": "gross 130%"},
        {"doc": "knowledge_base/02_income_rules.md", "snippet": "duplicate"},
        {"doc": "knowledge_base/01_program_overview.md", "snippet": "overview"},
        {"snippet": "no doc key"},  # ignored gracefully
    ]
    summary = handoff_summary(state, _result_for(state), citations)
    assert summary is not None
    assert "RULE CITATIONS" in summary
    assert summary.count("knowledge_base/02_income_rules.md") == 1
    assert "knowledge_base/01_program_overview.md" in summary


def test_handoff_summary_works_without_citations():
    state = _complete_state()
    summary = handoff_summary(state, _result_for(state), None)
    assert summary is not None
    assert "RULE CITATIONS" not in summary


def test_handoff_summary_marks_volunteered_deductions_with_real_chain():
    # W31: the actual calculator honors the deduction chain. The handoff
    # block must show whatever the applicant volunteered, not stub copy.
    state = _complete_state(
        earned_monthly_income=1_600.0,
        dependent_care_monthly=300.0,
        shelter_costs_monthly=900.0,
        medical_expenses_monthly=200.0,
        has_elderly_or_disabled_member=True,
    )
    summary = handoff_summary(state, _result_for(state))
    assert summary is not None
    assert "Earned (wage) monthly income: $1,600.00" in summary
    assert "Dependent-care costs (monthly): $300.00" in summary
    assert "Shelter costs (monthly): $900.00" in summary
    assert "Medical expenses (monthly): $200.00" in summary


# ---------------------------------------------------------------------
# 3. Agent package — local-only, exactly summary + banner
# ---------------------------------------------------------------------

def test_agent_handoff_package_is_local_only_summary_and_banner():
    agent = FoodPathAgent(llm_client=_NoopLLM())
    agent.state = _complete_state()
    agent._eligibility_result = _result_for(agent.state)
    package = agent.handoff_summary()
    assert package is not None
    # W39 cut: no email fields, no outbound notification. The package
    # is the dependency-free local summary plus the banner.
    assert set(package) == {"summary", "banner"}
    assert package["banner"] == _HANDOFF_BANNER
    assert "ready to share with a case worker" in package["banner"]
    assert "CASE-WORKER HANDOFF SUMMARY" in package["summary"]
