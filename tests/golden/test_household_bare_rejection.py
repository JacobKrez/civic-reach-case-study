"""W68b regression: a bare rejection of the recorded household_size, with
no replacement value anywhere else in the message, must clear the field
and re-ask -- not be silently ignored.

Live human review, 2026-07-28. After a misparse recorded
household_size=1, the user replied "no not 1 person" -- an unambiguous
rejection of that exact value. It matched neither the existing
``correction_cue`` (requires "actually" / "sorry" / "i meant" / "no
wait" / etc. -- a bare "no" does not qualify) nor ``household_phrase_cue``
(requires "of us" / "there are" / "household of" / etc.), so the
existing W64 out-of-order household-size correction branch never fired.
The wrong household_size=1 silently survived, and the live transcript's
next reply asked about BOTH household size and income in the same
message -- the model improvising around a state it could see was
contradicted by the user's words but had no clean way to reconcile.

The fix adds a dedicated, code-templated (not model-generated) bare-
rejection branch: when the user rejects a number that matches the
CURRENTLY recorded household_size, and no OTHER number appears anywhere
in the message that could serve as a replacement, the field is cleared
and the canonical next-field question is re-asked deterministically --
the same "don't leave wrong data in place, and don't let the model
improvise the recovery" principle already established for W60's
explicit-restart handling.
"""

from __future__ import annotations

import os

os.environ.setdefault("GUARDRAIL_CLASSIFIER", "off")

import pytest

from src.agent import FoodPathAgent


class _SilentLLM:
    """Bare empty tool-call stub -- forces every reply through the
    deterministic fallback/correction layers, the path that actually
    misfired in the 2026-07-27/28 human review.
    """

    def chat(self, messages, system=None, tools=None):
        if isinstance(system, str) and "intent classifier" in system.lower():
            return {"content": "normal", "tool_calls": []}
        return {"content": "", "tool_calls": []}


def test_w68b_bare_rejection_clears_household_size():
    """The exact live-review reply must clear household_size, not leave
    the wrong value of 1 silently in place.
    """
    agent = FoodPathAgent(llm_client=_SilentLLM())
    agent.turn("1")
    assert agent.state.household_size == 1

    agent.turn("no not 1 person")
    assert agent.state.household_size is None, (
        "W68b: a bare rejection of the recorded household_size with no "
        "replacement number must clear the field, not leave the wrong "
        f"value in place; got {agent.state.household_size!r}"
    )


def test_w68b_bare_rejection_also_clears_a_cached_verdict():
    """If a (wrong, premature) verdict had already been computed, a bare
    rejection of household_size must invalidate it too -- the same
    stale-verdict-survives-a-correction bug class W60 fixed for restarts.
    """
    agent = FoodPathAgent(llm_client=_SilentLLM())
    agent.turn("1")
    agent.turn("2000")
    agent.turn("no")
    assert agent.eligibility_result is not None

    agent.turn("no not 1 person")
    assert agent.eligibility_result is None, (
        "W68b: rejecting household_size after a verdict was computed "
        "must invalidate the cached verdict, not leave a stale result "
        f"pinned to the wrong household size; got {agent.eligibility_result!r}"
    )


def test_w68b_reply_asks_a_single_clean_question_not_two():
    """The reply must ask ONE question (household size), not two fields
    at once. This is the deterministic-reply guard against the live
    duplicate-question bug (the model asking about household size AND
    income in the same message after seeing a state it couldn't
    reconcile).
    """
    agent = FoodPathAgent(llm_client=_SilentLLM())
    agent.turn("1")
    reply = agent.turn("no not 1 person")

    question_marks = reply.count("?")
    assert question_marks == 1, (
        "W68b: the re-ask reply must contain exactly one question, not "
        f"a duplicate/compound question; got {question_marks} in {reply!r}"
    )
    assert "income" not in reply.lower(), (
        "W68b: the re-ask reply must not also ask about income in the "
        f"same breath; got {reply!r}"
    )


def test_w68b_mismatched_rejection_does_not_clear():
    """Rejecting a number that does NOT match the recorded household_size
    must not fire this branch -- e.g. a stray "not 1" that doesn't refer
    to the actual recorded value.
    """
    agent = FoodPathAgent(llm_client=_SilentLLM())
    agent.turn("2")
    assert agent.state.household_size == 2

    agent.turn("no not 1 person")
    assert agent.state.household_size == 2, (
        "W68b: a rejection of a number that does not match the recorded "
        f"household_size must not clear it; got {agent.state.household_size!r}"
    )


def test_w68b_rejection_with_a_real_replacement_does_not_fire_bare_path():
    """A rejection that DOES carry another number elsewhere in the
    message must not be treated as a bare (replacement-less) rejection
    -- it should fall through to other handling rather than blindly
    clearing the field.
    """
    agent = FoodPathAgent(llm_client=_SilentLLM())
    agent.turn("1")
    assert agent.state.household_size == 1

    agent.turn("no not 1 person, actually 3")
    # The bare-rejection branch specifically must not have fired (it
    # would have cleared to None); whatever value survives is owned by
    # other correction logic, not this guard.
    assert agent.state.household_size is not None, (
        "W68b: a rejection WITH a replacement number present must not be "
        "swallowed by the bare-rejection guard (which only handles the "
        f"no-replacement case); got {agent.state.household_size!r}"
    )


def test_w68b_household_size_never_set_does_not_fire():
    """With no household_size recorded yet, there is nothing to reject --
    this branch must not fire (it requires an existing value to compare
    against)."""
    agent = FoodPathAgent(llm_client=_SilentLLM())
    assert agent.state.household_size is None
    # Should not raise, and should not enter the bare-rejection branch's
    # clear-and-reask path (there's nothing cached to clear).
    agent.turn("no not 1 person")
    # Whatever the fallback did with this as a first-turn reply is out of
    # this guard's scope; the only contract here is "no crash, and the
    # bare-rejection audit event is not recorded".
    assert not any(
        entry.get("event") == "field_cleared" for entry in agent._audit
    ), "W68b: bare-rejection clear must not fire when there was nothing to reject"
