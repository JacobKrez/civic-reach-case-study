"""F2 tripwires \u2014 record enumerated household counts; confirm in the ack.

The doc (CONVERSATION_OVERHAUL.md \u00a79.3 F2) changes the prompt's
behavior on enumerated household compositions. Pre-F2: the
"do not put arithmetic or inferred values in that tool call"
rule caused MiniMax to refuse to record "me + my two kids + my
mom" because counting is *arithmetic* in MiniMax's reading, and
to ask a confirmation question ("sound right?") that nothing
resolves.

Post-F2: counting people the user explicitly listed is RECORDING,
not arithmetic. The new few-shot example spells this out, and the
acknowledge-and-ask rule says "state the count back; the user can
veto, and the correction machinery handles the veto."

This file's tripwires:
  (a) Prompt-content tests: the new few-shot example is present
      in both EN and ES, and the prompt rule explicitly mentions
      that counting enumerated people is recording, not arithmetic.
  (b) Behavioral test: a mock LLM that simulates a model which
      *did* follow the prompt (calls update_state(household_size=4)
      on the same turn it receives "me, my two kids, and my mom")
      produces a reply with a stated-back count and the canonical
      next-field question, NOT a "sound right?" dangling confirmation.

Both tripwires are failing-before on the pre-F2 prompt.
"""

from __future__ import annotations

import json
import re

import pytest


# ---------------------------------------------------------------------------
# Prompt content tests
# ---------------------------------------------------------------------------


def test_f2_prompt_has_new_fewshot_for_enumerated_household_en():
    """F2 adds an explicit few-shot example showing that counting
    enumerated people ("me, my two kids, and my mom") is RECORDING,
    not arithmetic. The example should call update_state with the
    counted value AND state the count back to the user.
    """
    from src.agent import SYSTEM_PROMPT
    fewshot = SYSTEM_PROMPT[SYSTEM_PROMPT.index("# Few-shot examples"):]
    # The few-shot must contain the verbatim pattern: enumerated
    # household composition + tool call to record the count + ack
    # restating the count.
    assert "two kids" in fewshot, (
        "F2: EN few-shot must contain the enumerated-household example"
    )
    assert "mom" in fewshot.lower(), (
        "F2: EN few-shot must contain 'mom' (or equivalent) for the example"
    )
    # The example must include a tool call that records household_size.
    # Use a regular string (no r prefix) so the inner " can be escaped
    # with \"; the raw-string form r"...\"..." has invalid syntax because
    # \" inside r"..." parses as literal backslash followed by closing ".
    assert re.search(
        "household_size[\"\\']?\\s*[:=]\\s*4\\b",
        fewshot,
        re.IGNORECASE,
    ), "F2: EN few-shot must call update_state(household_size=4)"
    # The example's ASSISTANT REPLY must NOT be a "sound right?"
    # confirmation — the user can veto a stated count, but a
    # confirmation question is what F2 is fixing.
    # Note: the F2 preamble discusses "sound right?" as the
    # anti-pattern to avoid; that's allowed. What we forbid is
    # the EXAMPLE itself leaving an unresolved confirmation.
    new_example_idx = fewshot.find("Example 5")
    if new_example_idx == -1:
        new_example_idx = 0  # no Example 5 found; fall through to whole-section check
    example_section = fewshot[new_example_idx:]
    # Strip the preamble paragraph (which discusses "sound right?"
    # as the anti-pattern). The preamble ends at the first "User:"
    # line in Example 5.
    user_idx = example_section.find("\nUser:")
    if user_idx != -1:
        # Only the example body (from "User:" onward) must not
        # contain a dangling confirmation question.
        example_body = example_section[user_idx:]
        assert "sound right" not in example_body.lower(), (
            "F2: Example 5's assistant reply must not contain "
            "'sound right' — the ack is the stated-back count, "
            "not a dangling confirmation"
        )
        # The example body must state the count back to the user.
        assert "4 of you" in example_body or "4 personas" in example_body, (
            "F2: Example 5 must state the count back (4 of you / 4 personas)"
        )


def test_f2_prompt_has_new_fewshot_for_enumerated_household_es():
    """ES parity: the same example exists in Spanish."""
    from src.agent import SYSTEM_PROMPT_ES
    fewshot_es = SYSTEM_PROMPT_ES[
        SYSTEM_PROMPT_ES.index("# Ejemplos de few-shot"):
    ]
    # Spanish translation of the example. The example should call
    # update_state(household_size=4) and ack with "4 personas" or
    # equivalent.
    assert "hijos" in fewshot_es or "niños" in fewshot_es, (
        "F2: ES few-shot must contain 'hijos'/'niños' (or equivalent)"
    )
    assert "mam" in fewshot_es.lower(), (
        "F2: ES few-shot must contain 'mamá' (or equivalent)"
    )
    assert re.search(
        "household_size[\"\\']?\\s*[:=]\\s*4\\b",
        fewshot_es,
        re.IGNORECASE,
    ), "F2: ES few-shot must call update_state(household_size=4)"
    # The example's ASSISTANT REPLY must not be a confirmation
    # question — the preamble discusses it as anti-pattern (allowed),
    # the example body must not use it.
    new_example_idx = fewshot_es.find("Ejemplo 5")
    if new_example_idx == -1:
        new_example_idx = 0
    example_section = fewshot_es[new_example_idx:]
    user_idx = example_section.find("\nUsuario:")
    if user_idx != -1:
        example_body = example_section[user_idx:]
        assert "te parece bien" not in example_body.lower(), (
            "F2: Ejemplo 5's reply must not contain 'te parece bien'"
        )
        assert "4" in example_body, (
            "F2: Ejemplo 5 must include the count '4' in the ack"
        )


def test_f2_prompt_rule_distinguishes_counting_from_arithmetic_en():
    """The prompt's rule section must explicitly say that counting
    people the user explicitly listed is RECORDING, not arithmetic.
    This is the doc's required rule change.
    """
    from src.agent import SYSTEM_PROMPT
    rules = SYSTEM_PROMPT[SYSTEM_PROMPT.index("# Rules"):]
    # The rule must distinguish 'counting people the user listed' from
    # 'eligibility arithmetic'. The test is keyword-based to allow
    # future wording changes.
    assert "count" in rules.lower() or "contar" in rules.lower(), (
        "F2: rules section must mention counting"
    )
    assert (
        "record" in rules.lower() or "recording" in rules.lower()
    ), "F2: rules section must mention recording"
    # The rule must NOT be the old monolithic "no arithmetic or inferred
    # values" rule applied to counting. Specifically, the rules must
    # say counting IS allowed (recording) so the model doesn't refuse.
    assert re.search(
        r"count(?:ing)?\s+(?:is\s+)?(?:recording|allowed|ok|ok to record)",
        rules,
        re.IGNORECASE,
    ), (
        "F2: rules must explicitly say counting enumerated people is "
        "recording (not arithmetic). Found neither 'count is recording' "
        "nor 'counting is recording' nor 'counting is allowed' nor "
        "'counting is ok to record'."
    )


def test_f2_prompt_rule_distinguishes_counting_from_arithmetic_es():
    from src.agent import SYSTEM_PROMPT_ES
    reglas = SYSTEM_PROMPT_ES[SYSTEM_PROMPT_ES.index("# Reglas"):]
    assert "contar" in reglas.lower() or "cuenta" in reglas.lower(), (
        "F2: reglas deben mencionar contar"
    )
    assert (
        "registr" in reglas.lower()
    ), "F2: reglas deben mencionar registrar"
    # Spanish version of the counting-as-recording rule.
    assert re.search(
        r"contar\s+(?:personas\s+)?(?:es|equivale a)\s+registr",
        reglas,
        re.IGNORECASE,
    ), (
        "F2: reglas deben decir que contar personas es registrar "
        "(no aritm\u00e9tica). No se encontr\u00f3 'contar es registrar' "
        "ni variantes."
    )


# ---------------------------------------------------------------------------
# Behavioral test
# ---------------------------------------------------------------------------


class _EnumeratedHouseholdLLM:
    """A model that follows the F2 prompt correctly: when the user
    enumerates household members, the model counts them and calls
    update_state(household_size=N) in the SAME turn, then acks
    with a stated-back count.

    This is the F2 target behavior; the unit test asserts that the
    agent's REPLY honors it (ack contains the count, no dangling
    confirmation, canonical next-field question appended).
    """

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, system=None, tools=None):
        self.calls += 1
        ctx = json.loads(messages[-1]["content"])
        missing = ctx.get("missing_fields") or []
        if missing and missing[0] == "household_size":
            # Simulate a model that followed the F2 few-shot:
            # counted the user's enumeration, called update_state,
            # and produced an ack restating the count + the next
            # field question. No 'sound right?'.
            return {
                "content": (
                    "So that's 4 of you \u2014 you, your two kids, "
                    "and your mom. If that's off, just say so. "
                    "Now, what's your household's gross monthly income?"
                ),
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 4}}
                ],
            }
        return {"content": "Got it.", "tool_calls": []}


def test_f2_enumerated_household_records_in_same_turn():
    """The F2 tripwire from the doc: probe message 2 records
    household_size=4 in that same turn, AND the reply contains
    no unresolved 'sound right' confirmation. The reply must
    contain a stated-back count ('4 of you' or equivalent) and
    the canonical next-field question.
    """
    from src.agent import FoodPathAgent

    llm = _EnumeratedHouseholdLLM()
    agent = FoodPathAgent(llm_client=llm)

    reply = agent.turn("well its me and my two kids, and my mom stays with us most of the time, does she count?")

    # State: household_size recorded (this is the F2 contract).
    assert agent.state.household_size == 4, (
        f"F2: household_size must be 4 after the turn; got {agent.state.household_size}"
    )
    # Missing fields: income and elderly are still open.
    missing = agent.state.missing_fields() or []
    assert "gross_monthly_income" in missing, missing
    assert "has_elderly_or_disabled_member" in missing, missing

    # Reply content invariants.
    lowered = reply.lower()
    # The stated-back count is in the reply.
    assert "4" in reply, (
        f"F2: reply must contain the stated-back count '4'; got {reply!r}"
    )
    # No unresolved 'sound right?' confirmation \u2014 the user can
    # veto a stated count, but a confirmation question is what F2
    # is fixing.
    assert "sound right" not in lowered, (
        f"F2: reply must not contain 'sound right' \u2014 the ack is "
        f"the count, not a dangling confirmation. reply={reply!r}"
    )
    # The canonical next-field question is appended.
    assert "what is your household's gross monthly income" in lowered, (
        f"F2: reply must ask the canonical income question. reply={reply!r}"
    )


# ---------------------------------------------------------------------------
# Negative test: a model that DOES NOT follow the prompt (still asks
# 'sound right?') produces a reply with a dangling confirmation. The
# agent must not strip this \u2014 the F2 contract is on the prompt,
# not on a post-hoc fix-up. This tripwire asserts the F2 fix is
# prompt-only, not a behavior-side bandage.
# ---------------------------------------------------------------------------


class _DanglingConfirmLLM:
    """A model that does NOT follow the F2 prompt: it asks
    'sound right?' instead of recording the count.
    """

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, system=None, tools=None):
        self.calls += 1
        ctx = json.loads(messages[-1]["content"])
        missing = ctx.get("missing_fields") or []
        if missing and missing[0] == "household_size":
            # Pre-F2 behavior: do not record, ask a confirmation.
            return {
                "content": (
                    "OK \u2014 she usually counts \u2014 so that would be "
                    "4 people total. Does that sound right, or\u2026"
                ),
                "tool_calls": [],
            }
        return {"content": "Got it.", "tool_calls": []}


def test_f2_no_behavior_bandage_for_dangling_confirm():
    """If the model produces a 'sound right?' confirmation without
    recording, the agent must NOT silently fix it. The F2 fix is
    the prompt; the agent's job is to ship what the model says.
    Recording on a confirmation-asking turn would be a behavior
    bandage that hides a prompt regression.

    This is the negative case: a model that violates F2's prompt
    should produce a reply with a dangling confirmation. If the
    agent ever *does* record household_size on this turn, the
    F2 prompt change is hiding model regressions instead of
    fixing the prompt.
    """
    from src.agent import FoodPathAgent

    llm = _DanglingConfirmLLM()
    agent = FoodPathAgent(llm_client=llm)

    reply = agent.turn("me and my mom and my two kids")

    # The model asked for confirmation without recording. The
    # agent must NOT silently fill household_size from the prose
    # (\u00a78 invariant: no scanning raw prose into state).
    assert agent.state.household_size is None, (
        f"F2: agent must NOT record household_size when the model "
        f"asked a confirmation \u2014 the F2 fix is the prompt, not a "
        f"behavior-side bandage. state={agent.state.household_size}"
    )
    # The reply contains the dangling confirmation.
    assert "sound right" in reply.lower() or "\u2026" in reply, (
        f"F2 negative: the dangling confirmation should be visible "
        f"in the reply (the model produces it; the agent does not "
        f"fix it). reply={reply!r}"
    )
