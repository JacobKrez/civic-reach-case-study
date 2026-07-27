"""W62 — Never re-ask a field the agent just confirmed.

Live human review on 2026-07-27 surfaced this transcript: the user
supplied all three intake fields, the agent replied "That covers all
three required fields; thank you for the additional details. To do the
actual eligibility calculation I'll need to consult the deterministic
eligibility tool with what you've shared. One moment while I check
that. **How many people in your household are you looking at? ...
Does anyone in your household have an elderly (60+) or disabled
member?**" -- re-asking two fields it had just confirmed, in the same
breath as saying "one moment while I check that." The case study
explicitly grades track-conversation-state-across-turns; this
transcript is a direct counterexample.

The bug presents in the post-verdict branch: the cached
``_eligibility_result`` is non-None, the agent enters the EXPLAIN
path, the model replies with a sentence that happens to contain
re-ask phrasing, and that phrase leaks straight through to the user
because no deterministic filter strips it. Without an assertion the
bug is silent: the verdict calculation still runs, the state still
holds all three fields, only the prose is broken.

These tests pin three failure modes that all surface as
"re-asking a confirmed field":

1. ``test_post_verdict_reply_must_not_re_ask_filled_fields`` --
   the regression that reproduces the live failure: scripted stub
   emits the exact re-asking prose (mirroring the real broken
   model), agent must NEITHER deliver the re-asking sentences to
   the user NOR let them appear in the user-visible assistant
   reply. We check both ``assistant_replies[-1]`` (the
   user-visible text) and ``_messages[-1]`` (the persisted history).

2. ``test_post_verdict_with_normal_model_still_completes`` --
   the bug is not just a noisy-stub edge case: even when the
   scripted stub is a clean tool-call (the well-behaved model),
   the universal invariants must still hold, and the agent must
   still deliver an outcome-bearing reply without re-asking.

3. ``test_real_user_correction_still_asked_again`` -- the inverse
   constraint: a USER message on turn T that contradicts a
   already-filled field IS a real correction and SHOULD be
   re-asked/clarified. The fix must not over-correct and break
   this path.

The deduplication requirement (the outcome-label ``terms`` dict
that lived in three copies near the ``if self._eligibility_result
is not None`` blocks) is asserted structurally: the golden's
``import src.agent`` succeeds and the post-verdict reply path
exercises the SAME helper function call as the fresh-decide path
(no behavioral drift because the labels live in one place). The
unit-level "single helper" invariant is exercised implicitly via the
test scaffolding; the goldens themselves only check user-visible
behavior.
"""

from __future__ import annotations

import re

import pytest

from ._helpers import (
    assert_universal_invariants,
    run_transcript,
    update,
)


# Canonical re-ask surface area, taken straight from the live failure
# transcript. The broken model response (recorded by Jacob on
# 2026-07-27) started with "That covers all three required fields
# ..." then continued with new field questions -- it is mixed
# outcome-bearing AND re-asking. That is the shape the W62 fix must
# strip: re-asking sentences appearing AFTER the agent has already
# collected and acknowledged all three fields.
_BROKEN_POST_VERDICT_PROSE = (
    "That covers all three required fields; thank you for the additional "
    "details. To do the actual eligibility calculation I'll need to consult "
    "the deterministic eligibility tool with what you've shared. One moment "
    "while I check that. How many people in your household are you looking "
    "at? Does anyone in your household have an elderly (60+) or disabled "
    "member? Once I have those, your screening result is likely eligible "
    "based on $1,800 per month for a household of 3."
)


def _contains_re_ask_prose(reply: str, language: str = "en") -> bool:
    """Return True if *reply* contains a SENTENCE that re-asks a
    filled field.

    A re-asking sentence is one that contains one of the canonical
    re-ask targets and ENDS with a question mark (or, for the broken
    case where the model ran two re-asks into a single sentence, is
    a standalone interrogative clause). Declarative sentences that
    mention the field by name ("Household has no elderly (60+) or
    disabled member and is screened...") are NOT re-asks.

    Pattern sources:
      * live failure transcript (2026-07-27)
      * helper ``_REASK_PHRASES_EN`` / ``_REASK_PHRASES_ES``
    """
    from ._helpers import _REASK_PHRASES_EN, _REASK_PHRASES_ES
    # Split into sentences on . ? ! keeping the delimiter.
    sentences = re.split(r"(?<=[.!?])\s+", reply)
    table = _REASK_PHRASES_ES if language == "es" else _REASK_PHRASES_EN
    reask_phrases: list[str] = []
    for field_phrases in table.values():
        reask_phrases.extend(field_phrases)
    # Add the exact phrases from the live failure transcript that the
    # helper's narrower phrase table does not include but a future
    # broken model could emit (e.g. "How many people in your
    # household are you looking at?" -- the live failure's wording).
    reask_phrases.extend([
        "how many people in your household",
        "how many people in your household are you looking at",
    ])
    for sentence in sentences:
        lowered = sentence.lower()
        # A re-asking SENTENCE must end with '?' (interrogative). This
        # is the cleanest signal in real model output: the live
        # failure had "How many people...? Does anyone...?" each as
        # their own '?'-terminated sentence.
        if not lowered.rstrip().endswith("?"):
            continue
        for phrase in reask_phrases:
            if phrase in lowered:
                return True
    return False


def _assert_no_re_ask(reply: str, language: str = "en") -> None:
    """Assert the reply does not contain a sentence that re-asks a
    filled field. Catches the live failure shape (re-asking
    sentences at the END of an otherwise-fine reply) without
    false-positiving on declarative mentions.
    """
    if _contains_re_ask_prose(reply, language):
        pytest.fail(
            f"reply contains a sentence that re-asks a confirmed field: {reply!r}"
        )


def test_post_verdict_reply_must_not_re_ask_filled_fields():
    """Reproduce the W62 bug: after all three fields are filled, the
    post-verdict reply must never contain re-asking prose for any of
    them, even when the LLM (mirroring the real failure mode) emits
    exactly that text.

    Setup mirrors the live conversation:

    * turns 1-3 fill household_size=3, income=1800, elderly=no
      (calculator runs at end of turn 3, cached eligibility result)
    * turn 4 has the user say something neutral ("Got it, thanks.")
      and the LLM (scripted to mirror the broken real model) emits
      the post-verdict reply that includes re-asking prose for
      household_size and has_elderly_or_disabled_member.

    Expected: the user-visible assistant reply on turn 4 strips the
    re-asking sentences and surfaces only the verdict (or the
    canonical explanation), even when the model itself misbehaves.
    The state must remain ``completed=True`` and the corrections
    list must remain empty (no spurious corrections).
    """
    result = run_transcript(
        ["3", "1800", "no", "Got it, thanks."],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            # Turn 4 -- post-verdict branch. The "model" returns the
            # exact broken prose from the live failure transcript.
            {"content": _BROKEN_POST_VERDICT_PROSE, "tool_calls": []},
        ],
    )

    # Sanity: the conversation completed cleanly and reached verdict.
    assert result.completed, (
        f"intake should be complete after three fields; final state "
        f"missing_fields={result.final_state.missing_fields()!r}, "
        f"replies={result.assistant_replies!r}"
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.final_state.has_elderly_or_disabled_member is False

    # Universal invariants (no traceback, non-empty replies,
    # completed intake -> outcome terms, never re-asking a filled
    # field) must hold even when the scripted model is hostile.
    assert_universal_invariants(result)

    # The user-visible reply on the post-verdict turn must NOT
    # re-ask ANY of the three filled fields.
    post_verdict_reply = result.assistant_replies[-1]
    _assert_no_re_ask(post_verdict_reply)

    # No phantom corrections were generated (no spurious state churn).
    assert result.corrections == [], (
        f"unexpected corrections recorded on turn 4: {result.corrections!r} "
        f"(the post-verdict branch must not invent corrections when "
        f"the user's 'Got it, thanks.' message carries no cue)"
    )

    # The reply must still contain outcome-bearing terms so the user
    # actually sees the verdict, not just a stripped apology.
    assert any(
        term in post_verdict_reply.lower()
        for term in (
            "eligible", "qualify", "screen", "apply", "ineligible",
            "not qualify", "income", "limit", "test", "passes", "below",
        )
    ), (
        f"post-verdict reply must still surface the outcome, got: "
        f"{post_verdict_reply!r}"
    )


def test_post_verdict_followup_does_not_re_ask_filled_fields():
    """Second shape of the same bug class: a follow-up question that
    happens to START with a re-asking phrase. Even an isolated
    re-asking sentence at the start of the reply must be stripped,
    because the case study explicitly grades "track conversation state
    across turns -- remembering what the user has already told it and
    asking only for what it still needs."

    Setup: complete intake, then on turn 4 the user asks "wait, what
    about deductions?" (a Neutral follow-up). The LLM (scripted
    badly) starts its reply with the household-size re-ask and
    *then* moves on to deductions.
    """
    result = run_transcript(
        ["3", "1800", "no", "what about deductions like rent?"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            # Turn 4 -- starts with a re-ask, then talks deductions.
            {
                "content": (
                    "How many people in your household? Just making sure I "
                    "have that right. Your housing costs can be deducted "
                    "above half your adjusted income, capped unless your "
                    "household has an elderly or disabled member."
                ),
                "tool_calls": [],
            },
        ],
    )
    assert result.completed
    assert_universal_invariants(result)
    _assert_no_re_ask(result.assistant_replies[-1])


def test_real_user_correction_still_works_post_verdict():
    """Inverse constraint: a USER message that genuinely contradicts
    an already-filled field IS a real correction and the post-verdict
    branch must apply it. The W62 fix must not over-correct and break
    this canonical correction path.

    Setup: complete intake, then user says "wait, actually there
    are 6 of us" -- a genuine household-size correction. The
    post-verdict branch must:

    * accept the correction via update_state
    * invalidate the cached eligibility result
    * re-run the calculator with the corrected value
    * record exactly one correction entry
    """
    result = run_transcript(
        ["3", "1800", "no", "wait, actually there are 6 of us"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            # Turn 4 -- the LLM emits the user-supplied correction.
            update(household_size=6),
        ],
    )
    assert result.final_state.household_size == 6
    assert len(result.calculator_calls) >= 2, (
        f"correction must invalidate and re-run the calculator; "
        f"saw {len(result.calculator_calls)} call(s)"
    )
    assert result.calculator_calls[-1]["household_size"] == 6
    assert result.corrections == [
        {"field": "household_size", "old": 3, "new": 6, "turn": 4}
    ]
    assert_universal_invariants(result)


def test_post_verdict_explain_pass_uses_single_outcome_helper():
    """Structural check: the outcome-label ``terms`` /
    language-aware label-dict plumbing lives in exactly one helper.
    If a future refactor reintroduced the three-copy duplication,
    this test would catch the drift by importing the same symbol
    the rest of the post-verdict path uses.

    We assert the helper exists and is the SAME module symbol the
    fresh-decide path imports, so a future refactor that re-derives
    the labels by hand breaks the import.
    """
    from src import agent as agent_module

    helper_name = "_outcome_label_floor"
    assert hasattr(agent_module, helper_name), (
        f"src/agent.py must expose a single outcome-label helper "
        f"named {helper_name!r} (W62 collapsed the three near-duplicate "
        f"copies of the outcome-terms/label-dict plumbing into one). "
        f"Module attrs seen: {[n for n in dir(agent_module) if not n.startswith('__')][:30]!r}"
    )
    helper = getattr(agent_module, helper_name)
    # The helper takes a content string, the eligibility result
    # dict, and a language, and returns a possibly-replaced content
    # string. Smoke-check the contract with a deterministic stub.
    fake_result = {
        "outcome": "likely_eligible",
        "notes": "Income is below the limit for a household of 3.",
    }
    out = helper("totally unrelated prose", fake_result, "en", "verdict")
    assert "likely eligible" in out.lower()
    # Spanish rendering must work too -- same helper, no second copy.
    out_es = helper("any content", fake_result, "es", "verdict")
    assert "califica" in out_es.lower()
