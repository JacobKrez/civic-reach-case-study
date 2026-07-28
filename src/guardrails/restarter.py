"""Deterministic handler for the restarter persona -- explicit restart
or in-flight recap mid-intake.

W60 close-the-loop finding for the ``restarter`` persona named in the case
study. The persona abandons mid-intake, sometimes says "actually can we
start over", and later asks "sorry, where were we?" / "what did I already
tell you?". The agent's job is:

1. EXPLICIT RESTART -- when the user clearly asks to start over, the agent
   must CLEAR the recorded assessment state (household_size,
   gross_monthly_income, has_elderly_or_disabled_member, and the four
   optional deduction fields) and the four intake history slots. It does
   NOT silently keep half a discarded intake. Keeping stale fields
   "because the user might mean a soft restart" is the wrong default:
   the documented behaviour of this screen is one-shot per session and
   the user owns the reset signal. The reset MUST be auditable: a
   ``reset_intake`` audit entry is recorded so the red-team harness can
   confirm the reset actually fired (vs. the message looking like a
   restart but no state change happening).

2. RECAP -- when the user asks what they already told the agent, the
   agent must be able to summarise the current state without leaking
   optional deduction values back as if they were required. The recap
   is code-templated from ``AssessmentState`` -- the model does not
   improvise so it cannot accidentally echo a value the user changed
   their mind on, or invent fields that were never collected.

Both are wired into the agent's deterministic dispatch AFTER the
safety / contract guards (crisis, injection, scope, not-a-person,
volatile-rule) and BEFORE the informational handlers (process,
expedited, benefit amount, application status) and the LLM call. The
restart path goes through the same ``reset_intake`` helper the
W50 ``/session/reset`` button uses so there is exactly one reset
implementation -- no divergent re-implementations to keep in sync.

Design constraints from the chunk brief:

* The restart must clear COLLECTED state, not just acknowledge the
  user's words. A "no-op restart" that leaves
  ``household_size=3, gross_monthly_income=2200`` while saying "okay,
  let's start over" is the worst-case failure -- the user thinks they
  have a clean slate, the agent asks the next question, and a quiet
  state contradiction surfaces a few turns later ("you said earlier
  you had 3 people -- was that wrong?"). The reset is mandatory when
  the detector fires.

* The recap must name only the populated intake fields. An empty
  recap ("you haven't told me anything yet") is also acceptable --
  it tells the user the screen is genuinely at turn 1 without
  making up state. Underscoring what has NOT been asked yet is more
  honest than padding with the deduction defaults, which are 0.0
  for everyone.

* The reset must NOT clear the conversation history, language mode,
  or the guardrail bookkeeping. Clearing the message history would
  also unhook the W53 forward-progress floor (the floor watches the
  end of the conversation, not the start), and dropping the
  language flag would flip a Spanish conversation silently back to
  English mid-reset, which is the same W19 bug class. Only the
  intake fields are cleared.
"""

from __future__ import annotations

import re

from ..branding import PRODUCT_NAME


# Explicit restart phrases. The patterns are conservative: they only
# fire on a clear "start over" / "redo" / "begin again" intent, not on
# a soft "wait, hold on" pause. A restart that fires by accident is
# much worse than a restart that doesn't fire -- the user loses
# collected data and has to re-type it.
_RESTART_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "can we start over" / "can I start over"
    re.compile(
        r"\bcan\s+(?:we|i|you)\s+(?:please\s+)?(?:start\s+over|begin\s+over|redo\s+(?:this|that|the\s+screen|the\s+screening)|start\s+again|begin\s+again)\b",
        re.IGNORECASE,
    ),
    # "let's / let me start over"
    re.compile(
        r"\b(?:let['’]s|let\s+me)\s+start\s+over\b|\b(?:let['’]s|let\s+me)\s+begin\s+again\b",
        re.IGNORECASE,
    ),
    # "actually I need to start over" / "actually can we start over"
    re.compile(
        r"\b(?:actually|wait|hold\s+on)\b[^.?\n]{0,40}\bstart\s+over\b",
        re.IGNORECASE,
    ),
    # "I want to redo this / start again from scratch"
    re.compile(
        r"\b(?:i\s+want\s+to|i['’]d\s+like\s+to|i\s+need\s+to)\s+"
        r"(?:redo|restart|start\s+over|begin\s+over)\b",
        re.IGNORECASE,
    ),
    # "never mind, start fresh"
    re.compile(
        r"\b(?:never\s+mind|forget\s+it)[,.]?\s*(?:start\s+(?:over|fresh|again)|begin\s+again)\b",
        re.IGNORECASE,
    ),
    # Bare/direct request with no leading qualifier -- "start over",
    # "restart", "start again" sent as the whole message. Live human
    # review, 2026-07-28: this is the single most natural phrasing a
    # real user types, and every pattern above requires a lead-in
    # ("can we...", "let's...", "actually...") that a bare request
    # doesn't have. Reproduced: 0 of 5 live runs of a bare "start over"
    # reset state; the LLM fell through to handling it in free-form
    # prose and never once actually cleared the intake. Anchored to the
    # whole message (allowing "please" and trailing punctuation) rather
    # than matched anywhere in text, so an incidental mention buried in
    # an unrelated sentence still can't fire by accident -- the
    # conservative design principle in this module's docstring.
    re.compile(
        r"^\s*(?:please\s+)?(?:start\s+over|start\s+again|begin\s+again|"
        r"restart(?:\s+(?:this|that|the\s+screen|the\s+screening))?|"
        r"redo\s+(?:this|that|the\s+screen|the\s+screening))"
        r"\s*(?:please)?\s*[.!]?\s*$",
        re.IGNORECASE,
    ),
)


# Recap phrases. The detector fires on a clear request to be told
# what was already provided. It does NOT fire on a question about the
# process in general ("what happens after this?") or on an in-flight
# clarification of a single field.
_RECAP_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "sorry, where were we?"
    re.compile(
        r"\b(?:sorry|scuse\s+me|pardon)[,.]?\s*(?:where\s+(?:were|did\s+we)\s+(?:we|leave\s+off))\b",
        re.IGNORECASE,
    ),
    # "what did I already tell you?" / "what have I told you so far?"
    re.compile(
        r"\bwhat\s+(?:did|have)\s+i\s+(?:already\s+)?(?:told|said|given|shared)\s+you\b",
        re.IGNORECASE,
    ),
    # "what information have I given so far?" / "what do you have so far?"
    re.compile(
        r"\b(?:what\s+(?:information|data|answers?)\s+(?:have\s+)?i\s+(?:given|provided|shared))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+(?:do\s+you|have\s+you)\s+(?:already\s+)?(?:have|got|know)\s+(?:so\s+far|about\s+me)\b",
        re.IGNORECASE,
    ),
    # "can you remind me what I said?"
    re.compile(
        r"\b(?:can\s+you|could\s+you)\s+remind\s+me\s+(?:what\s+i\s+(?:said|told|answered))\b",
        re.IGNORECASE,
    ),
)


def detect_restart_request(text: str) -> bool:
    """Return True if *text* is an explicit restart request.

    Conservative: only fires on phrasing that names the action ("start
    over", "redo this", "begin again", "start fresh"). A user who says
    "wait, hold on a second" or "actually I need to fix something" is
    not asking to erase state and the detector returns False. A
    restart that fires by accident destroys real intake data and is
    much worse than a restart that doesn't fire.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    return any(pattern.search(text) for pattern in _RESTART_PATTERNS)


def detect_recap_request(text: str) -> bool:
    """Return True if *text* is a request for the agent to recap what
    has been collected so far.

    Conservative: only fires on phrasing that names the recap action
    ("what did I tell you", "where were we", "what do you have so
    far"). A user who asks "what comes next?" or "what do you still
    need from me?" is asking about MISSING fields, not the current
    recap, and the detector returns False -- those are handled by the
    W53 next-field question path, not by the restarter handler.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    return any(pattern.search(text) for pattern in _RECAP_PATTERNS)


def reset_intake_message(lang: str = "en") -> str:
    """Return the language-appropriate reset acknowledgement.

    The wording is intentionally short. The user asked to start over;
    a long preamble about "I'm clearing your data now" is unnecessary
    noise. The W53 forward-progress floor then appends the canonical
    next-field question (household size) so the user has somewhere to
    start again immediately.
    """
    if lang == "es":
        return (
            f"Entendido: vamos a empezar de nuevo. {PRODUCT_NAME} es solo "
            "una evaluación inicial: no se guarda como una solicitud y "
            "tampoco se envía a ninguna agencia. Comencemos desde el principio."
        )
    return (
        f"Got it -- let's start over. {PRODUCT_NAME} is only an initial "
        "screen: it is not saved as an application and it is not sent to "
        "any agency. Let's begin from the beginning."
    )


def _recap_label(field: str, lang: str) -> str:
    """Return the bilingual-friendly label for a populated field."""
    labels = {
        "en": {
            "household_size": "household size",
            "gross_monthly_income": "gross monthly income",
            "has_elderly_or_disabled_member": "an elderly (60+) or disabled member in the household",
        },
        "es": {
            "household_size": "tamaño del hogar",
            "gross_monthly_income": "ingresos mensuales brutos",
            "has_elderly_or_disabled_member": "una persona de 60+ años o con discapacidad en el hogar",
        },
    }
    return labels.get(lang, labels["en"]).get(field, field)


def _format_value(field: str, value: object) -> str:
    """Format a populated state value for the recap line.

    Human-friendly round-trip. Income is rendered with a dollar sign
    and no decimals (most users think in whole dollars for monthly
    income). Household size is rendered as a plain integer. The
    elderly/disabled flag is rendered as a yes/no.
    """
    if field == "gross_monthly_income" and isinstance(value, (int, float)):
        return f"${int(round(float(value)))}"
    if field == "household_size" and isinstance(value, int):
        return str(value)
    if field == "has_elderly_or_disabled_member":
        return "yes" if value else "no"
    return str(value)


def recap_message(state, lang: str = "en") -> str:
    """Return a code-templated recap of the current state.

    Walks ``AssessmentState.missing_fields()`` in the canonical
    priority order (household_size, gross_monthly_income,
    has_elderly_or_disabled_member) so the order matches the order the
    agent asks questions in -- the user sees "you told me X, Y is
    still missing" rather than an alphabetised dump.

    Only populated fields are named. The four optional deduction
    fields (earned_monthly_income, dependent_care_monthly,
    medical_expenses_monthly, shelter_costs_monthly) are deliberately
    OMITTED from the recap unless they are > 0 -- the W14 default is
    0.0 for everyone and surfacing those zeros would confuse a user
    who never volunteered them.

    The model does not author this string. The assistant boilerplate
    below is fixed so a user who asks "what did I tell you?" twice in
    a row always hears the same shape.
    """
    populated: list[str] = []
    missing: list[str] = []

    if state.household_size is not None:
        populated.append(
            f"{_recap_label('household_size', lang)}: {_format_value('household_size', state.household_size)}"
        )
    else:
        missing.append(_recap_label("household_size", lang))
    if state.gross_monthly_income is not None:
        populated.append(
            f"{_recap_label('gross_monthly_income', lang)}: {_format_value('gross_monthly_income', state.gross_monthly_income)}"
        )
    else:
        missing.append(_recap_label("gross_monthly_income", lang))
    if state.has_elderly_or_disabled_member is not None:
        populated.append(
            f"{_recap_label('has_elderly_or_disabled_member', lang)}: "
            f"{_format_value('has_elderly_or_disabled_member', state.has_elderly_or_disabled_member)}"
        )
    else:
        missing.append(_recap_label("has_elderly_or_disabled_member", lang))

    if not populated:
        if lang == "es":
            return (
                f"Aún no me has dicho nada. Esta evaluación inicial solo "
                f"necesita tres datos para {PRODUCT_NAME}: tamaño del "
                "hogar, ingresos mensuales brutos y si alguien tiene 60 "
                "años o más o una discapacidad."
            )
        return (
            f"You haven't told me anything yet. This initial screen only "
            f"needs three pieces of information for {PRODUCT_NAME}: "
            "household size, gross monthly income, and whether anyone in "
            "the household is 60+ or disabled."
        )

    populated_text = "; ".join(populated)
    if not missing:
        if lang == "es":
            return f"Esto es lo que me dijiste hasta ahora: {populated_text}. El siguiente paso es el resultado de la evaluación."
        return f"So far you've told me: {populated_text}. The next step is the screening result."

    missing_text = ", ".join(missing)
    if lang == "es":
        return (
            f"Esto es lo que me dijiste hasta ahora: {populated_text}. "
            f"Aún necesito: {missing_text}."
        )
    return (
        f"So far you've told me: {populated_text}. "
        f"I still need: {missing_text}."
    )


def apply_intake_reset(state) -> None:
    """Clear the AssessmentState's collected intake fields in place.

    Mutates the passed ``AssessmentState`` so the agent loop's next
    ``state.missing_fields()`` call returns all three required fields.
    The four optional deduction fields are also cleared so a user who
    starts over does not carry forward old amounts they wanted to
    discard.

    Deliberately does NOT touch:
      * ``turn_count`` -- the conversation has already happened and
        the count is a true historical record; resetting it would
        silently rewind the W53 forward-progress floor's frame of
        reference.
      * ``language`` -- a Spanish conversation must stay Spanish
        after the reset; clearing it would flip the response back to
        English mid-reset, the same W19 bug class.
      * ``is_crisis`` / ``is_out_of_scope`` / ``is_injection_attempt``
        -- these are guardrail bookkeeping flags, not intake fields.
        The reset only clears data; the safety flags stay as a
        historical record of what was already said.
    """
    state.household_size = None
    state.gross_monthly_income = None
    state.has_elderly_or_disabled_member = None
    state.earned_monthly_income = 0.0
    state.dependent_care_monthly = 0.0
    state.medical_expenses_monthly = 0.0
    state.shelter_costs_monthly = 0.0


__all__ = [
    "apply_intake_reset",
    "detect_recap_request",
    "detect_restart_request",
    "recap_message",
    "reset_intake_message",
]
