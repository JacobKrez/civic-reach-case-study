"""F1 tripwires — fold discipline: stop stapling questions.

The doc (CONVERSATION_OVERHAUL.md \u00a79.3) says F1 changes the W53
floor from a brittle substring/anchor match against the
canonical question to a simple question-presence rule:

  - If the reply contains NO '?' at all \u2192 append the canonical
    next-field question (the original W53 floor, unchanged).
  - If the reply ENDS with '?' \u2192 leave it alone, whatever the
    question is. A model that asked *some* question keeps the
    conversation moving; a duplicate canonical question
    visibly breaks it.
  - question_override always wins (F3 contract).

This file's tripwires assert the new rule from the doc and are
the failing-before check on working agreement #1.
"""

from __future__ import annotations

import pytest

from src.agent import _w53_fold_ask, _NEXT_FIELD_QUESTION


# A model reply that ends in a question mark gets nothing
# appended. This is the central F1 rule.
def test_f1_reply_ending_in_question_is_left_alone():
    """The model asked *some* question (\"Sound right?\"). F1 says
    leave it alone \u2014 don't staple the canonical question after
    it. Pre-F1 behavior would staple \"How many people...\"
    after \"Sound right?\" because the canonical-signature
    substring doesn't match \"Sound right?\".
    """
    content = "So that's 4 of you \u2014 you, your two kids, and your mom. Does that sound right?"
    missing = ["gross_monthly_income"]
    result = _w53_fold_ask(content, "en", missing)
    assert result == content, (
        f"F1: reply ending in '?' should be left alone; got {result!r}"
    )


# Same rule for the Spanish path.
def test_f1_reply_ending_in_question_is_left_alone_spanish():
    content = "Entonces son 4: t\u00fa, tus dos hijos y tu mam\u00e1. \u00bfTe parece bien?"
    missing = ["gross_monthly_income"]
    result = _w53_fold_ask(content, "es", missing)
    assert result == content, (
        f"F1 ES: reply ending in '\u00bf' should be left alone; got {result!r}"
    )


# Empty content gets the canonical question (the original W53
# behavior, preserved).
def test_f1_empty_content_appends_canonical():
    """If the model produces no reply at all, append the canonical
    next-field question. This is the original W53 floor and
    must still work after F1.
    """
    missing = ["household_size"]
    result = _w53_fold_ask("", "en", missing)
    canonical = _NEXT_FIELD_QUESTION["en"]["household_size"]
    assert result == canonical, (
        f"F1: empty content should produce the canonical question; got {result!r}"
    )


# Content with no '?' anywhere gets the canonical appended.
def test_f1_content_with_no_question_mark_appends_canonical():
    """The model produced prose but didn't ask anything. Append
    the canonical question (original W53 floor, preserved).
    """
    content = "OK \u2014 4 people. Got it."
    missing = ["gross_monthly_income"]
    result = _w53_fold_ask(content, "en", missing)
    assert "what is your household's gross monthly income" in result.lower(), (
        f"F1: content without '?' should still get canonical; got {result!r}"
    )


# The F3 contract: question_override must always win, even when
# the model already ended with a question. The override is a
# deterministic question that needs to surface.
def test_f1_question_override_wins_over_model_question():
    content = "Got it \u2014 4 people. Sound right?"
    missing = ["gross_monthly_income"]
    override = "Was that $300 per month, per week, or per day?"
    result = _w53_fold_ask(content, "en", missing, question_override=override)
    assert override in result, (
        f"F1/F3: question_override must surface even when model asked; got {result!r}"
    )


# Empty missing list is a no-op (preserved).
def test_f1_empty_missing_is_noop():
    content = "Sound right?"
    result = _w53_fold_ask(content, "en", [])
    assert result == content, (
        f"F1: empty missing must be a no-op; got {result!r}"
    )


# The headline regression: a chatty model that ends with its own
# confirmation question (terminated by a real `?`) does NOT get
# the canonical question stapled after it. This is the §9.1
# turn-2 failure case (rendered with a *real* `?`, not the
# trailing-ellipsis variant in the original transcript).
#
# Note on the §9.1 transcript: the actual turn-2 reply ends with
# "Does that sound right, or…" -- a trailing ellipsis, NOT a
# `?`. The F1 contract is "ends with `?`" (literal); trailing
# ellipsis falls through to the canonical-append branch. That is
# the documented tradeoff: a model that trails off (rather than
# asking a real question) still gets the canonical. The
# double-question visible in §9.1 is what F1 trades away.
# The unit tripwire here asserts the *positive* contract: when
# the model does end with a real `?`, the canonical is NOT
# appended.
def test_f1_chatty_confirmation_no_double_question():
    """The headline regression for F1: a model that ends its
    reply with a real `?` keeps the conversation moving on its
    own question. F1 must NOT append the canonical after.
    """
    content = (
        "OK — she usually counts — so that would be 4 people "
        "total. Does that sound right?"
    )
    missing = ["gross_monthly_income"]
    result = _w53_fold_ask(content, "en", missing)
    lowered = result.lower()
    assert "what is your household's gross monthly income" not in lowered, (
        f"F1 REGRESSION: canonical question was stapled. result={result!r}"
    )
    assert result == content, (
        f"F1: content must be returned verbatim when it ends in '?'; got {result!r}"
    )


# Spanish parity: the same chatty-confirmation case in ES, with a
# real inverted question mark (¿).
def test_f1_chatty_confirmation_no_double_question_spanish():
    content = (
        "OK — ella generalmente cuenta — entonces serían 4 "
        "personas en total. ¿Te parece bien?"
    )
    missing = ["gross_monthly_income"]
    result = _w53_fold_ask(content, "es", missing)
    assert "cuál es el ingreso mensual bruto" not in result.lower(), (
        f"F1 ES REGRESSION: canonical question stapled. result={result!r}"
    )
    assert result == content
