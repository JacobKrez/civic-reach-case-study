"""W61 regression: a comma-joined multi-number reply to an income question
must not be silently reduced to the leading number.

This is the same shape of input that surfaced during the 2026-07-27
human review. After a confusing re-ask from the agent, the user
replied "27, 0, and 100" to what the agent was presenting as an
income question. 27 was the household_size they had already given
two turns earlier, 0 was the actual income, and 100 was unrelated
or garbled. The previous fallback parser matched "27," as a leading
amount-with-details, fed the WHOLE string to ``normalize_income``,
which returned 27.0 -- and that wrong figure survived into the
final eligibility explanation.

The fix is documented in src/agent.py around ``_fallback_parse_terse_reply``
(``gross_monthly_income`` branch) and in the system-prompt tool-call
contract. The test exercises both:

  * the deterministic fallback (the path that actually misfired in
    the human review, via a provider that returns a bare tool call),
  * the LLM tool-call extraction path (the path the simulator is
    supposed to grade against, via a provider that emits the buggy
    multi-number input as an ``update_state`` call with the leading
    number).

The invariant we assert is the contract the fallback's docstring
already promises: a parse failure just leaves the field unset for
the LLM to re-ask, never misfiles into the wrong slot. For a
multi-number reply with no income cue, "decline to guess" is the
only outcome that honors that contract.
"""

from __future__ import annotations

import os

os.environ.setdefault("GUARDRAIL_CLASSIFIER", "off")

import pytest

from src.agent import FoodPathAgent
from tests.golden._helpers import RecordingLLM


# ---------------------------------------------------------------------------
# Provider fixtures
# ---------------------------------------------------------------------------
#
# Both providers below are intentionally minimal: they emit ONLY tool calls
# (no assertion-text prose), they keep the main queue in lock-step with the
# turns that consume them, and they route the bug-replicating input through
# the same path the real conversation took on 2026-07-27.

class _LeadingNumberOnlyLLM:
    """Bug-replicating provider: emits ``update_state{gross_monthly_income:
    <leading_number>}`` for the buggy multi-number reply. This is the path
    a real model could plausibly take on "27, 0, and 100" if it latched
    onto the first token. We want the agent's tool-call extraction gate
    to refuse to accept this update without an income cue.
    """

    def __init__(self) -> None:
        self._step = 0

    def chat(self, messages, system=None, tools=None):
        # Classifier calls return normal.
        if isinstance(system, str) and "intent classifier" in system.lower():
            return {"content": "normal", "tool_calls": []}

        # Step 0: missing household_size -> take the scripted value (3).
        # Step 1: missing has_elderly_or_disabled_member -> take "no".
        # Step 2: missing gross_monthly_income -> user will reply with the
        #         multi-number string; this provider mis-emits 27.0 from
        #         the leading number.
        if self._step == 0:
            self._step += 1
            return {
                "content": "",
                "tool_calls": [{"name": "update_state", "input": {"household_size": 3}}],
            }
        if self._step == 1:
            self._step += 1
            return {
                "content": "",
                "tool_calls": [
                    {"name": "update_state", "input": {"has_elderly_or_disabled_member": False}}
                ],
            }
        # Step 2: model latches onto the leading "27" of "27, 0, and 100".
        self._step += 1
        return {
            "content": "",
            "tool_calls": [
                {"name": "update_state", "input": {"gross_monthly_income": 27.0}}
            ],
        }


class _NoToolCallForIncomeLLM:
    """Bug-replicating provider for the deterministic fallback path:
    emits nothing on the income turn, forcing the deterministic
    fallback (``_fallback_parse_terse_reply``) to handle the user's
    raw message. This is the path that actually misfired in the
    2026-07-27 review.
    """

    def __init__(self) -> None:
        self._step = 0

    def chat(self, messages, system=None, tools=None):
        if isinstance(system, str) and "intent classifier" in system.lower():
            return {"content": "normal", "tool_calls": []}

        if self._step == 0:
            self._step += 1
            return {
                "content": "",
                "tool_calls": [{"name": "update_state", "input": {"household_size": 3}}],
            }
        if self._step == 1:
            self._step += 1
            return {
                "content": "",
                "tool_calls": [
                    {"name": "update_state", "input": {"has_elderly_or_disabled_member": False}}
                ],
            }
        # Step 2: bare empty tool-call stub. The fallback will try to
        # parse "27, 0, and 100" as income.
        self._step += 1
        return {"content": "", "tool_calls": []}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_w61_deterministic_fallback_rejects_leading_number_from_multi_number():
    """The deterministic fallback MUST NOT extract $27 from "27, 0, and 100".

    Driving the deterministic fallback directly (no LLM tool call on the
    income turn). The previous behavior was to match "27," as a leading
    amount-with-details and feed the whole string to ``normalize_income``,
    which returned 27.0. After W61 the fallback declines to guess on
    multi-number replies with no income cue.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallForIncomeLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")

    # Before the bug-trigger turn, income is unset.
    assert agent.state.gross_monthly_income is None

    # The bug-replicating reply: a comma-joined list of bare numbers.
    agent.turn("27, 0, and 100")

    # W61 contract: the leading number (27) is NOT silently accepted as
    # income when the message carries no income cue AND multiple numbers
    # are present.
    assert agent.state.gross_monthly_income is None, (
        "W61: leading number from a multi-number reply with no income cue "
        "must not be silently accepted as gross_monthly_income; "
        f"got {agent.state.gross_monthly_income!r}"
    )


def test_w61_llm_tool_call_path_rejects_leading_number_from_multi_number():
    """The LLM update_state path MUST also refuse $27 from "27, 0, and 100".

    A real provider might latch onto the leading token of a multi-number
    reply and emit ``update_state{gross_monthly_income: 27.0}``. The
    agent's tool-call extraction gate (``_apply_tool_calls`` /
    ``_INTAKE_FIELDS`` contract) must reject this update when the user
    message carries no income cue and another field already has a value
    matching the rejected number.
    """
    agent = FoodPathAgent(llm_client=_LeadingNumberOnlyLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")

    assert agent.state.gross_monthly_income is None

    agent.turn("27, 0, and 100")

    # W61 contract holds for the LLM tool-call path too: a multi-number
    # reply with no income cue and a leading number that already belongs
    # to a different field this turn cannot land silently as income.
    assert agent.state.gross_monthly_income is None, (
        "W61: leading number from a multi-number reply must not survive "
        "the LLM tool-call extraction gate when no income cue is present; "
        f"got {agent.state.gross_monthly_income!r}"
    )


def test_w61_unambiguous_single_number_with_income_cue_still_works():
    """The W61 guard must not regress the legitimate terse-income path.

    "2100 a month" is unambiguous: a single number, with an explicit
    income cue. The fallback should still extract 2100.0 -- this is the
    case the W55/W64 work specifically enables.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallForIncomeLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")

    assert agent.state.gross_monthly_income is None
    agent.turn("2100 a month")
    assert agent.state.gross_monthly_income == 2100.0


def test_w61_unambiguous_single_number_dollar_form_still_works():
    """The W61 guard must not regress the bare-amount path.

    "$2,100" is unambiguous (currency-form, single number). The
    fallback's existing bare_amount branch should accept it.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallForIncomeLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")

    assert agent.state.gross_monthly_income is None
    agent.turn("$2,100")
    assert agent.state.gross_monthly_income == 2100.0


def test_w61_ambiguous_multi_number_emits_audit_log():
    """W61 must emit a telemetry marker when the ambiguous case is
    declined, so this regression is visible in the turn log.

    The agent exposes an ``_terse_income_ambiguous_log`` list (list of
    user-message strings that hit the decline-to-guess path). The test
    asserts the buggy reply is recorded there.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallForIncomeLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")
    agent.turn("27, 0, and 100")

    log = getattr(agent, "_terse_income_ambiguous_log", None)
    assert log is not None, (
        "W61: agent must expose _terse_income_ambiguous_log so the "
        "decline-to-guess case is observable in tests and telemetry."
    )
    assert any("27" in entry and "100" in entry for entry in log), (
        f"W61: expected the multi-number reply to be recorded in the "
        f"ambiguous-income log; got {log!r}"
    )


def test_w61_does_not_emit_audit_log_for_unambiguous_answers():
    """The W61 audit log must not fire on legitimate terse-income replies."""
    agent = FoodPathAgent(llm_client=_NoToolCallForIncomeLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")
    agent.turn("$2,100")

    log = getattr(agent, "_terse_income_ambiguous_log", [])
    assert log == [], (
        f"W61: unambiguous terse-income replies must not appear in the "
        f"ambiguous-income log; got {log!r}"
    )


def test_w61_agent_does_not_echo_wrong_income_in_reply():
    """The agent's reply must not confirm a wrong income figure.

    If the buggy extraction had succeeded (the W61-pre behavior), the
    agent would have replied with 'Got it -- $27 per month.' After W61
    the agent must NOT confirm any number from the multi-number reply
    as income in its assistant reply. We accept any of:
      * a re-ask about income,
      * an acknowledgment that does not contain "$27" as a confirmed figure.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallForIncomeLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")
    reply = agent.turn("27, 0, and 100")

    # The reply must not confirm $27 (or $0 or $100, since none of them
    # are confirmed income values either) as the user's income.
    assert "$27" not in reply, (
        f"W61: agent must not confirm $27 as income after a multi-number "
        f"reply with no cue; got reply: {reply!r}"
    )


# ---------------------------------------------------------------------------
# Pytest fixtures / parameter smoke
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_reply",
    [
        "27, 0, and 100",          # original bug shape (English)
        "27, 0, 100",              # no "and"
        "27 0 100",                # whitespace separators
        "27 and 0 and 100",        # only "and"
        "3, 1500, and 7",          # household_size=3 was already recorded
    ],
)
def test_w61_parametrized_multi_number_shapes(bad_reply):
    """The guard should decline any multi-number shape, not just the exact
    bug-replicating string. Each of these carries no income cue and
    contains 2+ bare numbers; reducing any of them to "the first one" is
    the same correctness bug.
    """
    agent = FoodPathAgent(llm_client=_NoToolCallForIncomeLLM())
    agent.turn("There are 3 of us in the house.")
    agent.turn("No elderly or disabled in our household.")
    assert agent.state.gross_monthly_income is None

    agent.turn(bad_reply)
    assert agent.state.gross_monthly_income is None, (
        f"W61: multi-number reply {bad_reply!r} must not be silently "
        f"reduced to a leading number; "
        f"got gross_monthly_income={agent.state.gross_monthly_income!r}"
    )
