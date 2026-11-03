"""Tests for the spoken confirmation protocol.

The asymmetry is the point: a rejection beats a confirmation, and a
partial match is never a confirmation. These tests pin both.
"""

from __future__ import annotations

import pytest

from src.voice.confirmation import (
    MAX_CONFIRMATION_ATTEMPTS,
    ConfirmationOutcome,
    classify_confirmation,
    should_escalate,
    should_reprompt,
)


class TestClearConfirmations:
    @pytest.mark.parametrize(
        "reply",
        ["yes", "yeah", "yep", "correct", "that's right", "sure", "okay", "right"],
    )
    def test_confirms(self, reply):
        assert classify_confirmation(reply) is ConfirmationOutcome.CONFIRMED


class TestClearRejections:
    @pytest.mark.parametrize(
        "reply",
        ["no", "nope", "not right", "that's wrong", "incorrect", "negative"],
    )
    def test_rejects(self, reply):
        assert classify_confirmation(reply) is ConfirmationOutcome.REJECTED


class TestNegationWins:
    """The dangerous case: a reply that contains both.

    "no that's not right" contains "right", which is a confirmation
    token. Reading it as a confirmation records a value the caller
    rejected.
    """

    @pytest.mark.parametrize(
        "reply",
        [
            "no that's not right",
            "no thats not right",
            "not correct",
            "no that is wrong",
            "that's not correct",
            "nope",
        ],
    )
    def test_negation_beats_confirmation(self, reply):
        assert classify_confirmation(reply) is ConfirmationOutcome.REJECTED

    def test_a_reply_with_both_tokens_is_a_rejection(self):
        reply = "no, yes it's not right"
        assert classify_confirmation(reply) is ConfirmationOutcome.REJECTED


class TestPartialMatchesAreNeverConfirmations:
    """A truncated "yes" is the recognizer's failure, not the caller's.

    Accepting it would record a value the caller never confirmed.
    """

    @pytest.mark.parametrize("reply", ["ye", "y", "yepp", "yess", "corr", "su"])
    def test_partial_is_unclear(self, reply):
        assert classify_confirmation(reply) is ConfirmationOutcome.UNCLEAR

    def test_single_letter_is_unclear(self):
        assert classify_confirmation("y") is ConfirmationOutcome.UNCLEAR


class TestRepeat:
    def test_repeat_is_recognized(self):
        assert classify_confirmation("repeat") is ConfirmationOutcome.REPEATED
        assert classify_confirmation("say that again") is ConfirmationOutcome.REPEATED


class TestUnclear:
    @pytest.mark.parametrize(
        "reply",
        ["", "   ", "maybe", "i dunno", "hm", "uh huh", "whatever"],
    )
    def test_unrecognised_is_unclear(self, reply):
        assert classify_confirmation(reply) is ConfirmationOutcome.UNCLEAR

    def test_ordinary_word_is_not_a_confirmation(self):
        """An intake answer is not a confirmation.

        A caller answering "two" is answering the household-size
        question. Treating that as a confirmation would be a category
        error that records the wrong thing.
        """
        assert classify_confirmation("two") is ConfirmationOutcome.UNCLEAR
        assert classify_confirmation("1500") is ConfirmationOutcome.UNCLEAR


class TestNormalisation:
    def test_punctuation_is_ignored(self):
        assert classify_confirmation("Yes!") is ConfirmationOutcome.CONFIRMED
        assert classify_confirmation("yes.") is ConfirmationOutcome.CONFIRMED
        assert classify_confirmation("  YES  ") is ConfirmationOutcome.CONFIRMED

    def test_typographic_quote_is_handled(self):
        assert (
            classify_confirmation("that’s right") is ConfirmationOutcome.CONFIRMED
        )

    def test_notebook_is_not_a_negation(self):
        """The negation check is token-wise, not substring-wise."""
        assert classify_confirmation("notebook") is ConfirmationOutcome.UNCLEAR


class TestAttemptLimits:
    def test_reprompt_until_the_limit(self):
        assert should_reprompt(1) is True
        assert should_reprompt(MAX_CONFIRMATION_ATTEMPTS - 1) is True

    def test_escalate_at_the_limit(self):
        assert should_escalate(MAX_CONFIRMATION_ATTEMPTS) is True
        assert should_escalate(MAX_CONFIRMATION_ATTEMPTS + 1) is True

    def test_no_escalation_below_the_limit(self):
        assert should_escalate(1) is False
        assert should_escalate(MAX_CONFIRMATION_ATTEMPTS - 1) is False

    def test_limit_is_at_least_two(self):
        """One retry is not a second chance."""
        assert MAX_CONFIRMATION_ATTEMPTS >= 2

    def test_reprompt_and_escalate_are_complementary(self):
        """They must not both be true, or the caller is asked again and
        handed off in the same turn."""
        for attempt in range(1, MAX_CONFIRMATION_ATTEMPTS + 3):
            assert not (should_reprompt(attempt) and should_escalate(attempt))
