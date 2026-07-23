"""Tests for the conversation-state schema in src/state.py."""

from __future__ import annotations

import pytest

from src.state import AssessmentState


def test_initial_state_has_all_three_intake_fields_missing():
    s = AssessmentState()
    missing = s.missing_fields()
    # All three required intake fields should be missing initially.
    assert "household_size" in missing
    assert "gross_monthly_income" in missing
    assert "has_elderly_or_disabled_member" in missing
    assert len(missing) == 3
    assert s.is_ready_to_calculate() is False


def test_missing_fields_returned_in_priority_order():
    """The order is intentional: cheapest/least-sensitive question first."""
    s = AssessmentState()
    assert s.missing_fields() == [
        "household_size",
        "gross_monthly_income",
        "has_elderly_or_disabled_member",
    ]


def test_setting_a_field_removes_it_from_missing_fields():
    s = AssessmentState()

    # Fill in household_size -> it should drop out, others remain.
    s2 = s.model_copy(update={"household_size": 3})
    missing = s2.missing_fields()
    assert "household_size" not in missing
    assert missing == ["gross_monthly_income", "has_elderly_or_disabled_member"]
    assert s2.is_ready_to_calculate() is False

    # Fill in gross_monthly_income next.
    s3 = s2.model_copy(update={"gross_monthly_income": 2000.0})
    assert s3.missing_fields() == ["has_elderly_or_disabled_member"]

    # Fill in the flag -> ready.
    s4 = s3.model_copy(update={"has_elderly_or_disabled_member": True})
    assert s4.missing_fields() == []
    assert s4.is_ready_to_calculate() is True


def test_setting_fields_out_of_order_still_clears_missing():
    """Order of *setting* shouldn't matter — only that all are set."""
    s = AssessmentState()
    s = s.model_copy(update={"has_elderly_or_disabled_member": False})
    assert s.missing_fields() == ["household_size", "gross_monthly_income"]
    s = s.model_copy(update={"gross_monthly_income": 0.0})
    assert s.missing_fields() == ["household_size"]
    s = s.model_copy(update={"household_size": 1})
    assert s.missing_fields() == []
    assert s.is_ready_to_calculate() is True


def test_guardrail_flags_do_not_affect_missing_fields():
    """is_crisis / is_out_of_scope / is_injection_attempt are not intake fields."""
    s = AssessmentState(
        is_crisis=True,
        is_out_of_scope=True,
        is_injection_attempt=True,
    )
    # Still all three intake fields missing — the guardrail flags are
    # classification signals, not user-provided intake answers.
    assert len(s.missing_fields()) == 3
    assert s.is_ready_to_calculate() is False


def test_turn_count_defaults_to_zero_and_increments():
    s = AssessmentState()
    assert s.turn_count == 0
    s2 = s.model_copy(update={"turn_count": 5})
    assert s2.turn_count == 5


def test_invalid_household_size_rejected():
    """Validator should reject household_size < 1 or > 20."""
    with pytest.raises(Exception):
        AssessmentState(household_size=0)
    with pytest.raises(Exception):
        AssessmentState(household_size=21)


def test_invalid_income_rejected():
    """Validator should reject negative income."""
    with pytest.raises(Exception):
        AssessmentState(gross_monthly_income=-100.0)


def test_bool_flag_accepts_true_false_and_none_distinctly():
    a = AssessmentState(has_elderly_or_disabled_member=True)
    b = AssessmentState(has_elderly_or_disabled_member=False)
    c = AssessmentState()
    assert a.has_elderly_or_disabled_member is True
    assert b.has_elderly_or_disabled_member is False
    assert c.has_elderly_or_disabled_member is None
    # All three must still be flagged as missing until set to a real bool.
    assert a.is_ready_to_calculate() is False
    assert b.is_ready_to_calculate() is False
    assert c.is_ready_to_calculate() is False
