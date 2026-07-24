"""Tests for W17 case-worker handoff summary + best-effort notification.

Covers the three spec'd areas:
1. handoff_summary() content + gating (never producible before intake is
   complete and a deterministic eligibility result exists; reflects real
   state only).
2. Email path is skipped cleanly with no SMTP env configured — the
   default test-suite behavior, with NO real network calls.
3. With SMTP fully mocked, the attempt fires with the correct
   recipient/content, and any SMTP exception is caught and does not
   propagate to the caller.
"""

from __future__ import annotations

import pytest

from src import handoff as handoff_mod
from src.agent import CivicReachAgent
from src.calculator import calculate_eligibility
from src.handoff import (
    _HANDOFF_BANNER,
    handoff_summary,
    send_handoff_email,
    smtp_is_configured,
)
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
    agent = CivicReachAgent(llm_client=_NoopLLM())
    assert agent.handoff_summary() is None


def test_handoff_summary_never_calls_the_llm():
    agent = CivicReachAgent(llm_client=_NoopLLM())
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


def test_agent_handoff_package_shape_and_banner():
    agent = CivicReachAgent(llm_client=_NoopLLM())
    agent.state = _complete_state()
    agent._eligibility_result = _result_for(agent.state)
    package = agent.handoff_summary()
    assert package is not None
    assert set(package) == {"summary", "banner", "email_attempted", "email_sent"}
    assert package["banner"] == _HANDOFF_BANNER
    assert "ready to share with a case worker" in package["banner"]


# ---------------------------------------------------------------------
# 3. Email path — skipped cleanly with no SMTP env (default), mocked
#    attempt fires correctly, SMTP exceptions never propagate
# ---------------------------------------------------------------------

def test_smtp_is_configured_requires_all_four_vars():
    full = {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASS": "p", "SMTP_TO": "t"}
    assert smtp_is_configured(full) is True
    for missing in full:
        partial = {k: v for k, v in full.items() if k != missing}
        assert smtp_is_configured(partial) is False
    assert smtp_is_configured({}) is False


def test_email_skipped_cleanly_with_no_smtp_env(monkeypatch):
    # Default test-suite behavior: no SMTP env, no network calls at all.
    for var in handoff_mod.SMTP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    assert send_handoff_email("summary text") is False


def test_email_skipped_when_any_single_var_missing():
    env = {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASS": "p"}  # no SMTP_TO
    assert send_handoff_email("summary text", env) is False


class _FakeSMTP:
    """Context-manager SMTP double; records every interaction."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in = None
        self.sent = []
        self.tls = False
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)


def test_email_attempt_fires_with_correct_recipient_and_content():
    _FakeSMTP.instances = []
    env = {
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "2525",
        "SMTP_USER": "demo-sender@example.test",
        "SMTP_PASS": "secret",
        "SMTP_TO": "caseworker-demo@example.test",
    }
    ok = send_handoff_email("THE SUMMARY BODY", env, smtp_factory=_FakeSMTP)
    assert ok is True
    assert len(_FakeSMTP.instances) == 1
    server = _FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.example.test", 2525)
    assert server.logged_in == ("demo-sender@example.test", "secret")
    assert len(server.sent) == 1
    message = server.sent[0]
    assert message["To"] == "caseworker-demo@example.test"
    assert message["From"] == "demo-sender@example.test"
    assert "THE SUMMARY BODY" in message.get_content()


class _ExplodingSMTP(_FakeSMTP):
    def send_message(self, message):
        raise OSError("connection refused")


def test_smtp_exception_is_caught_and_never_propagates():
    env = {
        "SMTP_HOST": "smtp.example.test",
        "SMTP_USER": "u",
        "SMTP_PASS": "p",
        "SMTP_TO": "t@example.test",
    }
    # Must not raise; returns False and the caller's flow is unaffected.
    assert send_handoff_email("body", env, smtp_factory=_ExplodingSMTP) is False


def test_agent_handoff_onscreen_path_unaffected_without_smtp(monkeypatch):
    # The demo-meeting scenario: zero SMTP env, on-screen summary works.
    for var in handoff_mod.SMTP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    agent = CivicReachAgent(llm_client=_NoopLLM())
    agent.state = _complete_state()
    agent._eligibility_result = _result_for(agent.state)
    package = agent.handoff_summary()
    assert package is not None
    assert package["email_attempted"] is False
    assert package["email_sent"] is False
    assert "CASE-WORKER HANDOFF SUMMARY" in package["summary"]


def test_handoff_email_recipient_comes_from_env_only():
    # Guard against regression to a hardcoded recipient: with no SMTP_TO
    # the send is skipped even when everything else is configured.
    env = {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASS": "p"}
    _FakeSMTP.instances = []
    assert send_handoff_email("body", env, smtp_factory=_FakeSMTP) is False
    assert _FakeSMTP.instances == []
