"""Confirmation protocol for spoken turns.

On a text interface, a confirmation is a small friction point: the user
reads the field, types "yes", and moves on. On a voice line the same
confirmation is a genuine hazard, because the recognition error is
bidirectional and the consequence is asymmetric.

The hazard, stated precisely: a caller is asked whether the household
size they gave was two. The recognizer returns "no". The agent hears
"no", treats it as a rejection, and re-asks. That is a recoverable
error. Now consider the reverse: the caller says "yes" and the
recognizer returns "ye", or the caller says "no" and the recognizer
returns "no that's right". The first is a confirmation that was
accepted when it should not have been. The second is a rejection that
was read as a confirmation.

The second case is the dangerous one. A confirmation the agent accepted
records a value the caller never confirmed, and for an eligibility
determination that value ends up in a benefits screen.

So this module is deliberately asymmetric:

* **A rejection wins over a confirmation.** Any reply containing a
  negation is a rejection, even if it also contains a confirmation token.
  "no that's not right" is a rejection, not a confirmation.
* **A partial match is never a confirmation.** "ye" is not "yes". A
  recognizer that truncates produces ambiguous audio, and an ambiguous
  confirmation is re-asked rather than assumed.
* **Anything unrecognized is UNCLEAR**, and UNCLEAR re-prompts.

When the attempts run out, a person takes over. That is the same
handoff pattern the rest of the project uses for a stuck caller, and it
is the right answer here: a caller who cannot get a clear confirmation
out of the machine should not keep pressing buttons against a machine
that is not understanding them.

The attempt limit is a module constant rather than a configuration
value on purpose. A configurable limit is a limit that gets lowered
during a demo.
"""

from __future__ import annotations

import re
from enum import Enum


class ConfirmationOutcome(str, Enum):
    """What a spoken reply means as a confirmation."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNCLEAR = "unclear"
    REPEATED = "repeated"


# Replies that are unambiguously a confirmation.
_CONFIRM_PHRASES = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "yup",
        "yuppers",
        "yes please",
        "yes please",
        "correct",
        "that's correct",
        "thats correct",
        "that is correct",
        "that's right",
        "thats right",
        "that is right",
        "right",
        "sure",
        "okay",
        "ok",
        "affirmative",
        "that's it",
        "thats it",
        "you got it",
        "that is what i said",
    }
)

# Replies that are unambiguously a rejection.
_REJECT_PHRASES = frozenset(
    {
        "no",
        "nope",
        "no sir",
        "no ma'am",
        "not right",
        "that's wrong",
        "thats wrong",
        "that is wrong",
        "incorrect",
        "wrong",
        "not correct",
        "that's not correct",
        "thats not correct",
        "no that's wrong",
        "no thats wrong",
        "negative",
        "no that's not right",
        "no thats not right",
    }
)

# A reply asking the agent to say the field again.
_REPEAT_PHRASES = frozenset(
    {
        "repeat",
        "say that again",
        "again",
        "what did you say",
        "come again",
        "pardon",
        "sorry what",
    }
)

# Any occurrence of one of these marks the reply as a rejection, even if
# a confirmation token is also present.
_NEGATION_TOKENS = (
    "no",
    "nope",
    "not",
    "isn't",
    "is not",
    "wasn't",
    "was not",
    "wrong",
    "incorrect",
    "negative",
)

# How many unclear or rejected answers before a person takes over.
MAX_CONFIRMATION_ATTEMPTS = 3

# Punctuation and whitespace are stripped before matching, because a
# speech recognizer renders punctuation inconsistently and a caller
# saying "yes." must not be treated differently from one saying "yes".
_PUNCT = re.compile(r"[^a-z0-9' ]+")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace."""
    if not text:
        return ""
    lowered = text.lower().replace("’", "'").replace("‘", "'")
    stripped = _PUNCT.sub(" ", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def classify_confirmation(spoken: str) -> ConfirmationOutcome:
    """Classify a spoken reply as a confirmation, rejection, or unclear.

    The ordering of the checks is the safety property:

    1. A repeat request is handled first, because "say that again" often
       contains no confirmation token at all and would otherwise fall
       through to UNCLEAR.
    2. A negation anywhere in the reply makes it a rejection. This runs
       before the confirmation check on purpose: "no that's not right"
       contains "right", which is a confirmation token, and reading it
       as a confirmation is the exact failure this module exists to
       prevent.
    3. An exact confirmation phrase is a confirmation.
    4. An exact rejection phrase is a rejection.
    5. Everything else is unclear.
    """
    normalized = _normalize(spoken)
    if not normalized:
        return ConfirmationOutcome.UNCLEAR

    if normalized in _REPEAT_PHRASES:
        return ConfirmationOutcome.REPEATED

    # A negation anywhere wins. Check token-wise so "notebook" does not
    # count as a negation.
    tokens = normalized.split()
    has_negation = any(
        token in _NEGATION_TOKENS
        or any(token.startswith(neg + " ") for neg in _NEGATION_TOKENS)
        for token in tokens
    )
    for neg in _NEGATION_TOKENS:
        if " " in neg and neg in normalized:
            has_negation = True
            break
    if has_negation:
        return ConfirmationOutcome.REJECTED

    if normalized in _CONFIRM_PHRASES:
        return ConfirmationOutcome.CONFIRMED

    if normalized in _REJECT_PHRASES:
        return ConfirmationOutcome.REJECTED

    # A partial match on a confirmation word is never a confirmation.
    # "ye" is what a truncated "yes" sounds like; treating it as a
    # confirmation would record a value the caller did not confirm.
    for phrase in _CONFIRM_PHRASES:
        if normalized and phrase.startswith(normalized) and len(normalized) < len(phrase):
            return ConfirmationOutcome.UNCLEAR

    return ConfirmationOutcome.UNCLEAR


def should_reprompt(attempt: int) -> bool:
    """Whether to ask the question again.

    ``attempt`` is the number of attempts already made, starting at 1
    for the first answer.
    """
    return attempt < MAX_CONFIRMATION_ATTEMPTS


def should_escalate(attempt: int) -> bool:
    """Whether the call should be handed to a person.

    A caller who cannot get a clear confirmation out of the machine
    should not keep pressing against a machine that is not understanding
    them. At the limit, a human takes over.
    """
    return attempt >= MAX_CONFIRMATION_ATTEMPTS


__all__ = [
    "ConfirmationOutcome",
    "MAX_CONFIRMATION_ATTEMPTS",
    "classify_confirmation",
    "should_escalate",
    "should_reprompt",
]
