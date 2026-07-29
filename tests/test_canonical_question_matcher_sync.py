"""The canonical intake questions must satisfy the checkers that grade them.

Rewording a question for clarity is a normal, desirable change. The
hazard is that several matchers key off that wording:

* ``tests/golden/_helpers._w53_asks_for_field`` -- the forward-progress
  invariant, which the continuous simulator grades every conversation
  with. If a reworded question no longer matches, the simulator reports
  *correct* replies as violations and spends the night filing false
  findings.
* ``tests/golden/_helpers._W53_ANCHOR_PHRASES`` -- short phrases that must
  survive a model paraphrasing the question.
* ``scripts/simulate._INTAKE_QUESTION_MARKERS`` -- used to detect the same
  field being asked twice in one reply.

The question text itself now has a single source of truth
(``src.agent_questions.NEXT_FIELD_QUESTION``, imported rather than copied
by the helper). These tests pin the other half of the contract: whatever
the wording becomes, the graders still recognize it.

If one of these fails after an intentional rewording, the fix is to
update the anchor/marker phrases to match the new text -- not to revert
the wording.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "tests") not in sys.path:
    sys.path.insert(0, str(REPO / "tests"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from golden import _helpers  # noqa: E402
from src.agent import _NEXT_FIELD_QUESTION  # noqa: E402
from src.agent_questions import NEXT_FIELD_QUESTION  # noqa: E402

LANGUAGES = ("en", "es")
FIELDS = (
    "household_size",
    "gross_monthly_income",
    "has_elderly_or_disabled_member",
)


def test_helper_reads_the_same_object_as_the_agent():
    """No transcription anywhere: same dict, not merely equal contents."""
    assert _helpers._W53_NEXT_FIELD_QUESTION is NEXT_FIELD_QUESTION
    assert _NEXT_FIELD_QUESTION is NEXT_FIELD_QUESTION


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("field", FIELDS)
def test_canonical_questions_satisfy_their_own_matcher(language, field):
    """The forward-progress invariant must recognize the canonical text.

    This is the tripwire for the failure that silently breaks the
    simulator: a reworded question the grader no longer accepts.
    """
    question = NEXT_FIELD_QUESTION[language][field]
    assert _helpers._w53_asks_for_field(question, field, language), (
        f"the canonical {language}/{field} question is not recognized by "
        f"_w53_asks_for_field; update the matcher to fit the new wording. "
        f"question={question!r}"
    )


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("field", FIELDS)
def test_canonical_questions_are_intake_questions(language, field):
    """They must also pass the looser F1-era check (any intake question)."""
    question = NEXT_FIELD_QUESTION[language][field]
    assert _helpers._w53_asks_an_intake_question(question, language), question


@pytest.mark.parametrize("language", LANGUAGES)
def test_anchor_phrases_still_appear_in_the_question_they_anchor(language):
    """An anchor that no longer appears in its own question is dead code
    that silently stops protecting against paraphrase."""
    anchors_by_field = _helpers._W53_ANCHOR_PHRASES.get(language) or {}
    for field, anchors in anchors_by_field.items():
        question = _helpers._w53_norm_for_match(NEXT_FIELD_QUESTION[language][field])
        for anchor in anchors:
            assert _helpers._w53_norm_for_match(anchor) in question, (
                f"anchor {anchor!r} no longer appears in the {language}/{field} "
                f"question: {NEXT_FIELD_QUESTION[language][field]!r}"
            )


def test_double_question_markers_still_match_their_questions():
    """The simulator's duplicate-question detector keys off short markers;
    a rewording that drops them turns the detector into a no-op."""
    import simulate

    for field, markers in simulate._INTAKE_QUESTION_MARKERS.items():
        # The elderly marker set is keyed as "elderly" in the simulator.
        canonical_field = (
            "has_elderly_or_disabled_member" if field == "elderly" else field
        )
        matched = False
        for language in LANGUAGES:
            question = NEXT_FIELD_QUESTION[language][canonical_field].lower()
            if any(marker in question for marker in markers):
                matched = True
                break
        assert matched, (
            f"no marker in {markers!r} appears in either language's "
            f"{canonical_field} question; the duplicate-question check for "
            f"this field can no longer fire"
        )


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("field", FIELDS)
def test_every_canonical_question_actually_asks_something(language, field):
    question = NEXT_FIELD_QUESTION[language][field]
    assert "?" in question, question
    assert question.strip(), question
