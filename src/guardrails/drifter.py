"""Deterministic handler for the drifter persona — off-topic detours mid-intake.

W60 close-the-loop finding for the ``drifter`` persona named in the case
study. The persona wanders off-topic mid-intake and then returns on its
own; the agent's job is to redirect WITHOUT losing collected state and
WITHOUT restarting the intake from the top.

Two design constraints from the chunk brief:

1. The canned redirect wording must MATCH the trigger. A food-bank
   question answered with "I can't give legal advice" is the W53 bug
   class again — the same canned scope-redirect message dropped on
   unrelated topics. Each detected trigger has its own brief,
   trigger-aware acknowledgement.

2. The handler must NOT fire on questions that are actually in scope
   for a benefits screener. Specifically:

     * "is it true they're cutting benefits next year?" — a user
       asking about future changes to benefits they're applying for is
       asking an IN-SCOPE administrative question. This is the same
       family as the W43/W59 informational questions (process /
       benefit-amount / application-status); it is NOT a drifter
       aside and must reach the LLM path or the existing
       informational handlers (if any apply) — never the
       drifter redirect.

   The detector's positive list is therefore drawn from the actual
   simulator's off-topic phrases AND from phrases that share the same
   shape (a household-context aside that is not a benefits
   eligibility question). Phrases that mention benefits, eligibility,
   the application, payments, deductions, or the screening itself
   are explicitly excluded.

The handler is deterministic and code-templated. The model does not
get a chance to improvise the redirect, and the agent's existing
W53 forward-progress floor can keep the intake advancing on the
NEXT field after the redirect is delivered.

States touched:
  * ``state.household_size`` / ``state.gross_monthly_income`` /
    ``state.has_elderly_or_disabled_member`` — preserved verbatim.
    The drifter redirect is informational; the agent must not lose
    any field already recorded.
"""

from __future__ import annotations

import re

from ..branding import PRODUCT_NAME


# Phrase categories that are clearly off-topic ASIDES during an FNS
# intake — landlord gripes, bus-route complaints, neighbourhood
# gossip, kids' sick days, employment history, food-bank hours,
# family comparisons. Each entry is the LITERAL phrase the
# simulator's ``_drifter_reply`` produces (so the handler covers the
# simulator) AND a small set of natural-language variations that
# share the same shape. Substring matches are bounded with ``\\b`` on
# both sides so a household-context word like "kids" inside an
# income phrase ("we have 3 kids at home" — a household-size
# answer) does not false-fire.
#
# NOTE: ``"is it true they're cutting benefits next year?"`` is
# deliberately EXCLUDED. That phrase is an in-scope administrative
# question about the program the user is screening for; routing it
# to a redirect would discard a legitimate user question.
_OFF_TOPIC_ASIDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Land / housing / rent aside
    re.compile(
        r"\b(?:my|the|our|his|her)\s+landlord\b.{0,80}\b"
        r"(?:keeps|raises|raised|increasing|raised the rent)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\brent\s+(?:keeps|just)\s+(?:going up|raised|raises)\b",
        re.IGNORECASE,
    ),
    # Bus / transportation aside
    re.compile(
        r"\b(?:bus|train)\s+routes?\b.{0,80}\bchanged\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bit\s+takes\s+(?:an?\s+hour|forever|so long)\s+to\s+get\s+downtown\b",
        re.IGNORECASE,
    ),
    # Food bank / pantry hours aside (non-eligibility)
    re.compile(
        r"\b(?:food\s+bank|food\s+pantry|pantry)\b.{0,80}"
        r"\bopen\s+(?:on\s+)?(?:saturdays?|sundays?|weekends?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bis\s+the\s+(?:food\s+bank|food\s+pantry|pantry)\s+open\b",
        re.IGNORECASE,
    ),
    # Neighbour / county office gossip aside
    re.compile(
        r"\bmy\s+neighbour\b.{0,80}\b(?:lost|losing|lost)\s+(?:her|his|their)\s+paperwork\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bthe\s+county\s+(?:lost|losing|lost)\b.{0,60}\bpaperwork\b",
        re.IGNORECASE,
    ),
    # Kids' illness aside
    re.compile(
        r"\b(?:the\s+kids?|my\s+kids?|children)\s+(?:have\s+been|are)\s+sick\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bbeen\s+sick\s+all\s+(?:week|month|day)\b",
        re.IGNORECASE,
    ),
    # Plant-closure / employment-history aside
    re.compile(
        r"\bi\s+used\s+to\s+work\s+(?:at|in|for)\b.{0,60}\b(?:plant|factory|mill)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:plant|factory|mill)\s+(?:closed|shut|shut\s+down)\b",
        re.IGNORECASE,
    ),
    # Family / out-of-state comparison aside
    re.compile(
        r"\bmy\s+(?:sister|brother|cousin|mom|dad|mother|father|friend)\b"
        r".{0,80}\bin\s+(?:virginia|texas|tennessee|carolina|georgia|florida)\b"
        r".{0,60}\bgets?\s+(?:way\s+|much\s+)?more\b",
        re.IGNORECASE,
    ),
)


# In-scope guard words. A phrase that contains any of these is NOT a
# drifter aside — the user is asking a real question about benefits,
# eligibility, payments, deductions, or the screening itself, even
# if it happens to mention something the drifter list above would
# otherwise match on. The detector excludes any candidate whose
# normalised form contains one of these tokens.
_IN_SCOPE_TOKENS: tuple[str, ...] = (
    "benefits",
    "benefit",
    "benefit amount",
    "eligibility",
    "eligible",
    "income limit",
    "deduction",
    "deductions",
    "application",
    "applying",
    "apply",
    "screen",
    "screening",
    "snap",
    "fns",
    "payment",
    "payments",
    "deposit",
    "approval",
    "approve",
    "approved",
)


def _has_in_scope_token(text: str) -> bool:
    """Return True if *text* explicitly anchors itself to the FNS topic.

    The drifter asides drift into housing, transport, kids, and
    gossip without anchoring back to the screening. A user who DOES
    anchor — "is it true they're cutting benefits next year?",
    "will my snap deposit change?", "are there deductions for medical
    bills?" — is asking a real eligibility / benefits question. Even
    if the message also matches an aside pattern above, the in-scope
    anchor wins and the detector returns False.
    """
    lowered = text.casefold()
    return any(token in lowered for token in _IN_SCOPE_TOKENS)


def detect_drifter_aside(text: str) -> bool:
    """Return True if *text* is a drifter aside — an off-topic comment
    during an FNS intake that the agent should politely acknowledge
    without restarting the intake and without losing collected state.

    The match is conservative. Phrases that mention benefits,
    eligibility, deductions, the application, or the screening
    itself are deliberately excluded so an in-scope administrative
    question can never be misclassified as a drifter aside.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    # In-scope anchor always wins, even if an aside pattern matches.
    if _has_in_scope_token(text):
        return False
    return any(pattern.search(text) for pattern in _OFF_TOPIC_ASIDE_PATTERNS)


def drifter_redirect_message(text: str, lang: str = "en") -> str:
    """Return a trigger-aware redirect for *text*.

    Each detected trigger has a brief acknowledgement that names the
    aside (a household-context aside that the FNS screen cannot
    answer) without giving the wrong canned wording. The redirect
    always invites the user back to the FNS intake; the agent's W53
    forward-progress floor then appends the canonical next-field
    question so the user has something concrete to answer.

    The trigger detection here mirrors ``detect_drifter_aside`` so
    the wording always matches the trigger that fired it. If no
    specific trigger matched (a future phrase we haven't catalogued
    yet) we fall through to a generic drifter redirect that still
    does NOT use the scope "I can't give legal advice" wording.
    """
    if lang == "es":
        # Spanish redirects follow the same trigger-aware shape.
        if re.search(
            r"\b(?:my|the|our|his|her)\s+landlord\b.{0,80}\b"
            r"(?:keeps|raises|raised|increasing|raised the rent)\b",
            text,
            re.IGNORECASE,
        ) or re.search(r"\brent\s+(?:keeps|just)\s+(?:going up|raised|raises)\b", text, re.IGNORECASE):
            return (
                f"Lamento que tu alquiler te preocupe; eso queda fuera de lo que "
                f"{PRODUCT_NAME} puede ayudar. Soy una evaluación inicial de "
                "elegibilidad para FNS / SNAP de Carolina del Norte y solo "
                "puedo recoger tres datos — tamaño del hogar, ingresos mensuales "
                "brutos y si alguien tiene 60 años o más o una discapacidad. "
                "Para problemas de vivienda, tu oficina local del DSS o un "
                "programa de asistencia de alquiler puede ayudar. Sigamos con "
                "las preguntas de elegibilidad."
            )
        if re.search(r"\b(?:bus|train)\s+routes?\b.{0,80}\bchanged\b", text, re.IGNORECASE) or re.search(
            r"\bit\s+takes\s+(?:an?\s+hour|forever|so long)\s+to\s+get\s+downtown\b", text, re.IGNORECASE
        ):
            return (
                f"Entiendo que el transporte puede ser difícil; eso queda fuera "
                f"de lo que {PRODUCT_NAME} puede ayudar. Esta evaluación inicial "
                "solo necesita tres datos: tamaño del hogar, ingresos mensuales "
                "brutos y si alguien tiene 60 años o más o una discapacidad. "
                "Sigamos con las preguntas de elegibilidad."
            )
        if re.search(
            r"\b(?:food\s+bank|food\s+pantry|pantry)\b.{0,80}\bopen\s+(?:on\s+)?(?:saturdays?|sundays?|weekends?)\b",
            text,
            re.IGNORECASE,
        ) or re.search(
            r"\bis\s+the\s+(?:food\s+bank|food\s+pantry|pantry)\s+open\b", text, re.IGNORECASE
        ):
            return (
                f"No tengo los horarios de los bancos de alimentos locales; "
                f"eso queda fuera del alcance de {PRODUCT_NAME}. Esta evaluación "
                "inicial solo necesita tres datos: tamaño del hogar, ingresos "
                "mensuales brutos y si alguien tiene 60 años o más o una "
                "discapacidad. Para encontrar despensas locales, llama al 211 o "
                "visita https://www.foodbanknc.org. Sigamos con las preguntas "
                "de elegibilidad."
            )
        if re.search(
            r"\bmy\s+neighbour\b.{0,80}\b(?:lost|losing|lost)\s+(?:her|his|their)\s+paperwork\b",
            text,
            re.IGNORECASE,
        ) or re.search(
            r"\bthe\s+county\s+(?:lost|losing|lost)\b.{0,60}\bpaperwork\b", text, re.IGNORECASE
        ):
            return (
                f"Lamento que tu vecina haya tenido ese problema; eso no es "
                f"algo que {PRODUCT_NAME} pueda resolver. Esta evaluación "
                "inicial solo necesita tres datos: tamaño del hogar, ingresos "
                "mensuales brutos y si alguien tiene 60 años o más o una "
                "discapacidad. Sigamos con las preguntas de elegibilidad."
            )
        if re.search(
            r"\b(?:the\s+kids?|my\s+kids?|children)\s+(?:have\s+been|are)\s+sick\b",
            text,
            re.IGNORECASE,
        ) or re.search(r"\bbeen\s+sick\s+all\s+(?:week|month|day)\b", text, re.IGNORECASE):
            return (
                f"Espero que se mejoren pronto; eso queda fuera de lo que "
                f"{PRODUCT_NAME} puede ayudar. Esta evaluación inicial solo "
                "necesita tres datos: tamaño del hogar, ingresos mensuales "
                "brutos y si alguien tiene 60 años o más o una discapacidad. "
                "Sigamos con las preguntas de elegibilidad."
            )
        if re.search(
            r"\bi\s+used\s+to\s+work\s+(?:at|in|for)\b.{0,60}\b(?:plant|factory|mill)\b",
            text,
            re.IGNORECASE,
        ) or re.search(
            r"\b(?:plant|factory|mill)\s+(?:closed|shut|shut\s+down)\b", text, re.IGNORECASE
        ):
            return (
                f"Espero que hayas encontrado trabajo desde entonces; eso "
                f"queda fuera del alcance de {PRODUCT_NAME}. Esta evaluación "
                "inicial solo necesita tres datos: tamaño del hogar, ingresos "
                "mensuales brutos y si alguien tiene 60 años o más o una "
                "discapacidad. Sigamos con las preguntas de elegibilidad."
            )
        if re.search(
            r"\bmy\s+(?:sister|brother|cousin|mom|dad|mother|father|friend)\b.{0,80}"
            r"\bin\s+(?:virginia|texas|tennessee|carolina|georgia|florida)\b.{0,40}\bgets?\s+more\b",
            text,
            re.IGNORECASE,
        ):
            return (
                f"Cada estado tiene límites de elegibilidad diferentes, así que "
                f"lo que recibe un familiar fuera de Carolina del Norte no "
                f"indica lo que podrías recibir aquí. Esta evaluación inicial "
                "solo necesita tres datos: tamaño del hogar, ingresos mensuales "
                "brutos y si alguien tiene 60 años o más o una discapacidad. "
                "Sigamos con las preguntas de elegibilidad."
            )
        return (
            f"Lamento no poder ayudar con eso; {PRODUCT_NAME} solo realiza una "
            "evaluación inicial de elegibilidad para FNS / SNAP de Carolina "
            "del Norte y no puede abordar temas no relacionados. Sigamos con "
            "las preguntas de elegibilidad."
        )

    # English redirects.
    if re.search(
        r"\b(?:my|the|our|his|her)\s+landlord\b.{0,80}\b"
        r"(?:keeps|raises|raised|increasing|raised the rent)\b",
        text,
        re.IGNORECASE,
    ) or re.search(r"\brent\s+(?:keeps|just)\s+(?:going up|raised|raises)\b", text, re.IGNORECASE):
        return (
            f"I'm sorry the rent is stressing you out — that's outside what "
            f"{PRODUCT_NAME} can help with. I'm an initial eligibility screen "
            "for North Carolina Food and Nutrition Services (FNS / SNAP), and "
            "I only need three pieces of information: household size, gross "
            "monthly income, and whether anyone in the household is 60+ or "
            "disabled. For housing help, your county DSS office or a rental "
            "assistance program may be able to help. Let's get back to the "
            "eligibility questions."
        )
    if re.search(r"\b(?:bus|train)\s+routes?\b.{0,80}\bchanged\b", text, re.IGNORECASE) or re.search(
        r"\bit\s+takes\s+(?:an?\s+hour|forever|so long)\s+to\s+get\s+downtown\b", text, re.IGNORECASE
    ):
        return (
            f"I understand transportation can be hard — that's outside what "
            f"{PRODUCT_NAME} can help with. This initial screen only needs "
            "three pieces of information: household size, gross monthly "
            "income, and whether anyone in the household is 60+ or disabled. "
            "Let's get back to the eligibility questions."
        )
    if re.search(
        r"\b(?:food\s+bank|food\s+pantry|pantry)\b.{0,80}\bopen\s+(?:on\s+)?(?:saturdays?|sundays?|weekends?)\b",
        text,
        re.IGNORECASE,
    ) or re.search(
        r"\bis\s+the\s+(?:food\s+bank|food\s+pantry|pantry)\s+open\b", text, re.IGNORECASE
    ):
        return (
            f"I don't have the hours for local food banks — that's outside "
            f"what {PRODUCT_NAME} covers. This initial screen only needs "
            "three pieces of information: household size, gross monthly "
            "income, and whether anyone in the household is 60+ or disabled. "
            "To find a local pantry, dial 211 or visit "
            "https://www.foodbanknc.org. Let's get back to the eligibility "
            "questions."
        )
    if re.search(
        r"\bmy\s+neighbour\b.{0,80}\b(?:lost|losing|lost)\s+(?:her|his|their)\s+paperwork\b",
        text,
        re.IGNORECASE,
    ) or re.search(
        r"\bthe\s+county\s+(?:lost|losing|lost)\b.{0,60}\bpaperwork\b", text, re.IGNORECASE
    ):
        return (
            f"I'm sorry your neighbour had that experience — that's not "
            f"something {PRODUCT_NAME} can resolve. This initial screen "
            "only needs three pieces of information: household size, gross "
            "monthly income, and whether anyone in the household is 60+ or "
            "disabled. Let's get back to the eligibility questions."
        )
    if re.search(
        r"\b(?:the\s+kids?|my\s+kids?|children)\s+(?:have\s+been|are)\s+sick\b",
        text,
        re.IGNORECASE,
    ) or re.search(r"\bbeen\s+sick\s+all\s+(?:week|month|day)\b", text, re.IGNORECASE):
        return (
            f"I hope they're feeling better soon — that's outside what "
            f"{PRODUCT_NAME} can help with. This initial screen only needs "
            "three pieces of information: household size, gross monthly "
            "income, and whether anyone in the household is 60+ or disabled. "
            "Let's get back to the eligibility questions."
        )
    if re.search(
        r"\bi\s+used\s+to\s+work\s+(?:at|in|for)\b.{0,60}\b(?:plant|factory|mill)\b",
        text,
        re.IGNORECASE,
    ) or re.search(
        r"\b(?:plant|factory|mill)\s+(?:closed|shut|shut\s+down)\b", text, re.IGNORECASE
    ):
        return (
            f"I'm glad you're back at work — that's outside what "
            f"{PRODUCT_NAME} can help with. This initial screen only needs "
            "three pieces of information: household size, gross monthly "
            "income, and whether anyone in the household is 60+ or disabled. "
            "Let's get back to the eligibility questions."
        )
    if re.search(
        r"\bmy\s+(?:sister|brother|cousin|mom|dad|mother|father|friend)\b.{0,80}"
        r"\bin\s+(?:virginia|texas|tennessee|carolina|georgia|florida)\b.{0,40}\bgets?\s+more\b",
        text,
        re.IGNORECASE,
    ):
        return (
            f"Each state has different eligibility limits, so what a family "
            f"member gets outside North Carolina doesn't tell us what you "
            f"could get here. This initial screen only needs three pieces of "
            "information: household size, gross monthly income, and whether "
            "anyone in the household is 60+ or disabled. Let's get back to "
            "the eligibility questions."
        )
    return (
        f"I'm not able to help with that — {PRODUCT_NAME} is only an initial "
        "eligibility screen for North Carolina Food and Nutrition Services "
        "(FNS / SNAP), and I can't address unrelated topics. Let's get back "
        "to the eligibility questions."
    )


__all__ = [
    "detect_drifter_aside",
    "drifter_redirect_message",
]