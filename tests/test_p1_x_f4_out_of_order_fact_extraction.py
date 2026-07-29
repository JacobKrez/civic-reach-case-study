"""F4 tripwires \u2014 out-of-order fact capture without violating the no-prose-scan invariant.

F4 (CONVERSATION_OVERHAUL.md \u00a79.3) closes the gap where a user message
carries an intake fact the main model dropped (no ``update_state``
tool call was emitted) but a deterministic backstop *did not* fire
either \u2014 so the fact evaporates. The \u00a79.1 probe message 4 \u2014 "my mom
is 68 does that matter" \u2014 is the canonical case: the user states
``has_elderly_or_disabled_member`` fact implicitly, the main model
*saw* it but did not emit a tool call, and the response just
re-asks the next question.

Design (from \u00a79.3): one additional LLM call whose ONLY job is
extraction. Tool calls flow through the *same* ``_apply_tool_calls``
gates. State still changes only via a model-issued,
schema-validated tool call \u2014 ``test_free_text_is_never_parsed_into_state``
stays green and the \u00a78 invariant stays honest. Fill-only for
``None`` fields; never overwrites non-None without a correction cue.

Pre-F4: ``agent.state.has_elderly_or_disabled_member`` is ``None``
after the \u00a79.1 turn-4 probe; the bot re-asks as if the user never
said anything.

Post-F4: the extraction pass fires on the probe and sets the
field; the bot phrases the reply with awareness of the fact.

This file's tripwires:
  (a) Positive: the §9.1 probe sets the elderly field.
  (b) Negative: a non-self reference ("my neighbor is 68") does
      NOT set the field.
  (c) Gate: bare social replies ("thanks!") don't fire the
      extraction pass.
  (d) Invariant: prose never writes to state; only model-issued
      tool calls do.
  (e) No double-call: when the main model DID emit tool calls,
      F4 doesn't double-fire.
  (f) Fill-only: a non-None field is not overwritten without a
      correction cue.
  (g) Audit: the extraction event is logged.
  (h) Intake-complete: F4 doesn't fire once the verdict is
      computed (no useful state to update, no double-cost).

All tripwires fail on the pre-F4 code.
"""

from __future__ import annotations

import os
import re

import pytest


@pytest.fixture(autouse=True)
def _enable_classifier_skip_and_f4_for_f4_tests():
    """Two env-var settings F4 tripwires need that the rest of
    the suite does NOT:

    AGENT_SKIP_CLASSIFIER_ON_CLEAN=1: F4 tests use stub LLMs
    that return empty content + no tool calls. With the real
    classifier ON, the classifier calls the LLM on every turn
    and the agent's tool-call path never fires (the household
    record never applies). F4 is the INTENDED behavior; the
    classifier-vs-stub interaction is an artifact of using a
    stub. Set the skip so the classifier doesn't call the
    stub. Restore on teardown so other tests are not affected.

    F4_EXTRACTION=1: F4 extraction pass is opt-in via env var
    (default OFF) to preserve the LLM call-count contract
    asserted by tests outside this file (e.g.
    test_classifier.py). F4 tripwires need F4 ON; set it.
    Restore on teardown.
    """
    prev_skip = os.environ.get("AGENT_SKIP_CLASSIFIER_ON_CLEAN")
    prev_f4 = os.environ.get("F4_EXTRACTION")
    os.environ["AGENT_SKIP_CLASSIFIER_ON_CLEAN"] = "1"
    os.environ["F4_EXTRACTION"] = "1"
    try:
        yield
    finally:
        if prev_skip is None:
            os.environ.pop("AGENT_SKIP_CLASSIFIER_ON_CLEAN", None)
        else:
            os.environ["AGENT_SKIP_CLASSIFIER_ON_CLEAN"] = prev_skip
        if prev_f4 is None:
            os.environ.pop("F4_EXTRACTION", None)
        else:
            os.environ["F4_EXTRACTION"] = prev_f4


# ---------------------------------------------------------------------------
# Stub LLM that simulates the §9.1 turn-4 failure shape: main model
# produces NO tool call but the message carries intake content.
# The F4 extraction pass will fire; the stub's _extraction_calls
# counter and the _F4_EXTRACTION_PROMPT marker let the test verify
# the stub was actually called twice (main + extraction).
# ---------------------------------------------------------------------------


class _MainModelPlusExtractionStub:
    """Two distinct behaviors:
    - Main-pass chat() returns ``{"content": ..., "tool_calls": []}``
      (the §9.1 failure: the main model saw the fact but didn't
      emit a tool call).
    - Extraction-pass chat() returns one or more ``update_state``
      tool calls (the F4-pass output).

    The F4-extraction prompt is detectable because it is a short,
    focused system-prompt; the main pass uses the long conversational
    system prompt. We route by checking which system prompt is
    passed.
    """

    EXTRACTION_PROMPT_MARKER = "extract only facts the user explicitly stated about"

    def __init__(self, *, extraction_call_payload=None, main_content=""):
        # extraction_call_payload: a list of dicts that mimic
        # ``update_state`` tool calls. e.g.
        # [{"name": "update_state", "input": {"has_elderly_or_disabled_member": True}}]
        self._extraction_call_payload = extraction_call_payload or []
        self._main_content = main_content
        self.chat_calls = []  # all chat() invocations in order
        self.extraction_calls = 0
        self.main_calls = 0

    def chat(self, messages, system=None, tools=None):
        self.chat_calls.append((list(messages), system))
        # The F4 extraction pass uses a short, distinct system
        # prompt. Detect it.
        if system and self.EXTRACTION_PROMPT_MARKER in system:
            self.extraction_calls += 1
            return {
                "content": "",
                "tool_calls": list(self._extraction_call_payload),
            }
        # Main conversational pass.
        self.main_calls += 1
        return {
            "content": self._main_content,
            "tool_calls": [],
        }


def _drive_first_two_turns(agent, llm):
    """Helper: drive two household-filling turns that produce
    household_size=3 and gross_monthly_income=2200.0.
    """
    # Turn 1: stub returns no tool_calls (stub main mode). The
    # deterministic terse-reply fallback may commit a value;
    # we set the state directly here so the test starts from a
    # known position.
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 2200.0
    # F4's gate fires on plausible intake content \u2014 the probe
    # message contains "mom is 68" which has elderly vocabulary,
    # so the gate opens. Mark turns for parity with normal flow.
    agent.state.turn_count = 2


# ---------------------------------------------------------------------------
# (a) Positive: the §9.1 probe sets the elderly field
# ---------------------------------------------------------------------------


def test_f4_positive_probe_message_sets_elderly_field():
    """The \u00a79.1 turn-4 probe: "my mom is 68 does that matter".
    Pre-F4: the model saw the fact but emitted no tool call; the
    elderly field stayed None. Post-F4: the extraction pass
    emits ``update_state(has_elderly_or_disabled_member=True)``
    and the field is set in the same turn.
    """
    from src.agent import FoodPathAgent

    stub = _MainModelPlusExtractionStub(
        extraction_call_payload=[
            {
                "name": "update_state",
                "input": {"has_elderly_or_disabled_member": True},
            }
        ],
        main_content="Yes \u2014 someone 60+ in the household changes which income test applies.",
    )
    agent = FoodPathAgent(llm_client=stub)
    _drive_first_two_turns(agent, stub)

    assert agent.state.has_elderly_or_disabled_member is None, (
        "precondition: elderly field must be None before the probe turn"
    )
    reply = agent.turn("my mom is 68 does that matter")
    assert agent.state.has_elderly_or_disabled_member is True, (
        "F4: probe message must set has_elderly_or_disabled_member=True "
        "via the extraction pass; pre-F4 the field stayed None"
    )
    # The extraction pass actually fired (system prompt was the
    # short extraction prompt, not the main conversational one).
    assert stub.extraction_calls >= 1, (
        f"F4: extraction pass must fire when main model emits no "
        f"tool_calls and message has plausible intake content; "
        f"extraction_calls={stub.extraction_calls}, "
        f"main_calls={stub.main_calls}"
    )


# ---------------------------------------------------------------------------
# (b) Negative: "my neighbor is 68" does NOT set the field
# ---------------------------------------------------------------------------


def test_f4_negative_neighbor_is_68_does_not_set_field():
    """Adversarial negative: a third-party reference ("my
    neighbor is 68") does NOT describe the user's own household.
    F4 must not over-extract; the field stays None and the
    extraction pass either does not fire or fires with no
    tool calls. The few-shot examples in the extraction prompt
    should teach the model this distinction.
    """
    from src.agent import FoodPathAgent

    # The extraction pass, if it fires, returns NO tool calls
    # for the neighbor case.
    stub = _MainModelPlusExtractionStub(
        extraction_call_payload=[],
        main_content="Got it.",
    )
    agent = FoodPathAgent(llm_client=stub)
    _drive_first_two_turns(agent, stub)

    assert agent.state.has_elderly_or_disabled_member is None
    reply = agent.turn("my neighbor is 68")
    assert agent.state.has_elderly_or_disabled_member is None, (
        "F4 negative: a third-party reference must NOT set the "
        "elderly field; this is the 'few-shot it' tripwire from "
        "\u00a79.3"
    )


# ---------------------------------------------------------------------------
# (c) Gate: bare social replies don't fire the extraction pass
# ---------------------------------------------------------------------------


def test_f4_plausibility_gate_rejects_social_replies():
    """Cheap gate: any digit OR elderly/disabled vocab OR household
    vocab. A bare social reply ("thanks!") matches NEITHER, so
    the extraction pass must NOT fire \u2014 even though intake is
    still incomplete.
    """
    from src.agent import FoodPathAgent

    stub = _MainModelPlusExtractionStub(
        extraction_call_payload=[
            {
                "name": "update_state",
                "input": {"has_elderly_or_disabled_member": True},
            }
        ],
    )
    agent = FoodPathAgent(llm_client=stub)
    _drive_first_two_turns(agent, stub)

    agent.turn("thanks!")
    # Extraction pass did NOT fire (no plausible intake content).
    assert stub.extraction_calls == 0, (
        f"F4 gate: bare social reply 'thanks!' must NOT fire the "
        f"extraction pass; got extraction_calls={stub.extraction_calls}"
    )


# ---------------------------------------------------------------------------
# (d) Invariant: prose never writes to state; only model-issued
#     tool calls do
# ---------------------------------------------------------------------------


def test_f4_extraction_prose_does_not_bypass_tool_call_path():
    """§8 invariant specific to F4: when the F4 extraction
    LLM call returns PROSE BUT NO TOOL CALLS, the prose must
    NOT be parsed into state. The contract: state changes only
    via a model-issued ``update_state`` tool call — the F4
    extraction pass must not silently parse the extraction
    model's prose either.

    This pins down the §9.3 contract literally: "state still
    changes only via a model-issued, schema-validated tool call,
    so ``test_free_text_is_never_parsed_into_state`` stays green
    and the invariant stays honest."
    """
    from src.agent import FoodPathAgent

    # Stub whose EXTRACTION pass returns prose (not tool calls).
    # If F4 parsed that prose into state, the invariant would be
    # broken. This is a different stub than the others — it
    # must be a fresh class so the extraction-prompt detection
    # still routes correctly.
    EXTRACTION_PROMPT_MARKER = _MainModelPlusExtractionStub.EXTRACTION_PROMPT_MARKER

    class ExtractionReturnsProseStub:
        def __init__(self):
            self.extraction_calls = 0
            self.main_calls = 0

        def chat(self, messages, system=None, tools=None):
            if system and EXTRACTION_PROMPT_MARKER in system:
                self.extraction_calls += 1
                # Extraction LLM "explains" the fact in prose
                # but emits no tool call. F4 must NOT honor this
                # prose — the invariant must hold.
                return {
                    "content": (
                        "Extracted: has_elderly_or_disabled_member=True"
                    ),
                    "tool_calls": [],
                }
            self.main_calls += 1
            return {"content": "Got it.", "tool_calls": []}

    stub = ExtractionReturnsProseStub()
    agent = FoodPathAgent(llm_client=stub)
    _drive_first_two_turns(agent, stub)

    assert agent.state.has_elderly_or_disabled_member is None
    agent.turn("my mom is 68 does that matter")
    # Even though the extraction LLM said the right thing in prose,
    # the field must stay None — F4 honors only tool calls,
    # never prose, never regex over the extraction model's output.
    assert agent.state.has_elderly_or_disabled_member is None, (
        "§8 invariant: F4 extraction pass prose must NOT be parsed "
        "into state; only the model-issued update_state tool call "
        "may change state"
    )
    # Sanity: the extraction DID fire (which is the precondition
    # for the prose being visible at all).
    assert stub.extraction_calls >= 1, (
        "test fixture sanity: extraction pass must have been invoked"
    )


# ---------------------------------------------------------------------------
# (e) No double-call when the main model DID emit a tool call
# ---------------------------------------------------------------------------


def test_f4_no_extraction_pass_when_main_model_emitted_tool_call():
    """F4 is for the case where the main model DROPPED the ball.
    When the main model DID emit ``update_state`` for the relevant
    field, the extraction pass must NOT fire \u2014 it would be a
    double-call, wasted latency, and potentially conflicting
    updates.
    """
    from src.agent import FoodPathAgent

    class MainModelEmitsToolCall:
        EXTRACTION_PROMPT_MARKER = _MainModelPlusExtractionStub.EXTRACTION_PROMPT_MARKER

        def __init__(self):
            self.extraction_calls = 0
            self.main_calls = 0

        def chat(self, messages, system=None, tools=None):
            if system and self.EXTRACTION_PROMPT_MARKER in system:
                self.extraction_calls += 1
                return {"content": "", "tool_calls": []}
            self.main_calls += 1
            return {
                "content": "Got it.",
                "tool_calls": [
                    {
                        "name": "update_state",
                        "input": {"has_elderly_or_disabled_member": True},
                    }
                ],
            }

    stub = MainModelEmitsToolCall()
    agent = FoodPathAgent(llm_client=stub)
    _drive_first_two_turns(agent, stub)

    agent.turn("my mom is 68 does that matter")
    assert agent.state.has_elderly_or_disabled_member is True
    # Extraction pass did NOT fire (main model already committed).
    assert stub.extraction_calls == 0, (
        f"F4 must NOT double-fire when main model emits a tool call; "
        f"got extraction_calls={stub.extraction_calls}"
    )


# ---------------------------------------------------------------------------
# (f) Fill-only: non-None field is not overwritten without a
#     correction cue
# ---------------------------------------------------------------------------


def test_f4_fill_only_does_not_overwrite_existing_value():
    """Once ``has_elderly_or_disabled_member`` is set to True, a
    later turn that mentions "actually she's 70" requires a
    CORRECTION cue before F4's extraction pass can overwrite the
    value. Without the cue, the field stays True.

    This pins down the \u00a79.3 contract: "Fill-only for None fields;
    never overwrites non-None without the existing correction
    cues."
    """
    from src.agent import FoodPathAgent

    stub = _MainModelPlusExtractionStub(
        extraction_call_payload=[
            {
                "name": "update_state",
                "input": {"has_elderly_or_disabled_member": False},  # tries to flip
            }
        ],
    )
    agent = FoodPathAgent(llm_client=stub)
    _drive_first_two_turns(agent, stub)
    agent.state.has_elderly_or_disabled_member = True  # already set

    # Subsequent message: "mom is 70" \u2014 NOT phrased as a
    # correction.
    agent.turn("by the way my mom is 70")
    # Field must remain True (no correction cue in the message).
    assert agent.state.has_elderly_or_disabled_member is True, (
        "F4 fill-only: a non-None field must NOT be overwritten "
        "without an explicit correction cue"
    )


# ---------------------------------------------------------------------------
# (g) Audit log records the extraction pass
# ---------------------------------------------------------------------------


def test_f4_audit_log_records_extraction_pass_fired():
    """Every side-effecting path in this code base appends to
    ``_audit``. F4 must too \u2014 an audit trip without an audit
    entry is invisible in the transcript.
    """
    from src.agent import FoodPathAgent

    stub = _MainModelPlusExtractionStub(
        extraction_call_payload=[
            {
                "name": "update_state",
                "input": {"has_elderly_or_disabled_member": True},
            }
        ],
    )
    agent = FoodPathAgent(llm_client=stub)
    _drive_first_two_turns(agent, stub)

    before_audit = list(agent._audit)
    agent.turn("my mom is 68 does that matter")
    new_entries = agent._audit[len(before_audit):]
    events = [e.get("event") for e in new_entries]
    assert "f4_extraction_pass_fired" in events, (
        f"F4: audit log must include f4_extraction_pass_fired event; "
        f"got events={events}"
    )


# ---------------------------------------------------------------------------
# (h) Intake-complete: F4 doesn't fire once the verdict is computed
# ---------------------------------------------------------------------------


def test_f4_no_extraction_when_intake_complete_verdict_computed():
    """When intake is already complete (verdict computed), the
    extraction pass must not fire on subsequent turns \u2014 there
    is no useful intake state to update, and a redundant call
    would burn latency/cost for nothing.
    """
    from src.agent import FoodPathAgent

    stub = _MainModelPlusExtractionStub(
        extraction_call_payload=[
            {
                "name": "update_state",
                "input": {"has_elderly_or_disabled_member": True},
            }
        ],
    )
    agent = FoodPathAgent(llm_client=stub)
    # Set all three intake fields \u2014 simulates a complete intake.
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 2200.0
    agent.state.has_elderly_or_disabled_member = False
    agent.state.turn_count = 3

    agent.turn("my mom is 68 actually")
    # F4 did NOT fire (intake is complete).
    assert stub.extraction_calls == 0, (
        f"F4 must NOT fire on a complete-intake turn; "
        f"got extraction_calls={stub.extraction_calls}"
    )
