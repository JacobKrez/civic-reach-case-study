"""Deterministic handler for "I already applied / I was denied / appeal" questions.

W43 close-the-loop finding: the current ``out_of_scope`` detector does
not catch "denied" / "appeal" / "I already applied" phrasings, so a
user in this situation gets a generic intake redirect from the model --
or, worse, only the calculator's intake fields and no acknowledgement
that this is a real, common situation. The right answer is a clean
acknowledgement + a redirect to the canonical NC FNS appeal path:

  1. Read the denial notice for the reason, deadline, and fair-hearing
     instructions.
  2. Contact the county DSS office if anything is unclear.
  3. CivicReach does not provide legal advice, and the appeal is
     outside the scope of this initial screen.

The handler is deterministic and code-templated. The intake is
preserved -- the response explicitly invites the user to continue with
the eligibility questions if they want a fresh check.
"""

from __future__ import annotations

import re


# English: phrases that indicate the user is already inside or past
# the SNAP/FNS application process (already applied, denied, appeal,
# hearing). The "applied" pattern is tightly scoped to the SNAP/FNS
# object so an unrelated "I applied for unemployment" does not match.
_APPLICATION_STATUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bi\s+(?:already\s+)?(?:applied|submitted\s+(?:a|an|the|my)\s+application)\s+(?:for|to)\s+(?:snap|fns|food\s*stamps?|food\s*assistance)\b", re.IGNORECASE),
    re.compile(r"\b(?:i\s+)?(?:already\s+)?(?:applied|filed)\s+for\s+(?:snap|fns|food\s*stamps?|food\s*assistance)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(?:snap|fns|food\s*stamps?)\s+application\s+(?:was|got|has\s+been)\s+(?:denied|rejected|turned\s+down)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(?:application|claim)\s+(?:was|got|has\s+been)\s+(?:denied|rejected|turned\s+down)\b", re.IGNORECASE),
    re.compile(r"\b(?:snap|fns|food\s*stamps?)\s+(?:was|got|has\s+been)\s+(?:denied|rejected|turned\s+down)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:was|got|been)\s+denied\s+(?:snap|fns|food\s*stamps?|benefits?)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+(?:do|can|should)\s+i\s+(?:appeal|request\s+a\s+(?:fair\s+)?hearing)\b", re.IGNORECASE),
    re.compile(r"\bappeal\s+(?:my|the|a)\s+(?:snap|fns|food\s*stamps?|denial|decision)\b", re.IGNORECASE),
    re.compile(r"\bfair\s+hearing\b", re.IGNORECASE),
    # "were" covers the plural subject ("my food stamps were cut"), which is
    # how users actually phrase this far more often than the singular forms.
    re.compile(r"\b(?:my|the)\s+(?:snap|fns|food\s*stamps?)\s+(?:was|were|got|has\s+been|have\s+been)\s+(?:cut|stopped|suspended)\b", re.IGNORECASE),
)

# Spanish: accent-insensitive.
_APPLICATION_STATUS_PATTERNS_ES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:ya\s+)?(?:aplique|solicite|presente\s+(?:la|mi|una)\s+solicitud)\b", re.IGNORECASE),
    re.compile(r"\bmi\s+solicitud\s+(?:fue|ha\s+sido)\s+(?:denegada|rechazada)\b", re.IGNORECASE),
    re.compile(r"\b(?:fui|me\s+han)\s+denegad[oa]\b", re.IGNORECASE),
    re.compile(r"\bcomo\s+(?:puedo|debo)\s+(?:apelar|solicitar\s+(?:una\s+)?audiencia)\b", re.IGNORECASE),
    re.compile(r"\bapelacion\s+(?:mi|de|a)\s+(?:snap|fns|cupones\s+de\s+alimentos|denegacion|decision)\b", re.IGNORECASE),
    re.compile(r"\baudiencia\s+(?:justa|administrativa)\b", re.IGNORECASE),
)

_SPANISH_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def detect_application_status_question(text: str) -> bool:
    """Return ``True`` when *text* is a post-application / appeal question."""
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.casefold()
    accentless = normalized.translate(_SPANISH_ACCENTS)
    if any(p.search(normalized) for p in _APPLICATION_STATUS_PATTERNS):
        return True
    if any(p.search(normalized) or p.search(accentless) for p in _APPLICATION_STATUS_PATTERNS_ES):
        return True
    return False


def application_status_response(lang: str = "en") -> str:
    """Return the deterministic post-application / appeal redirect.

    The response acknowledges the situation, routes the user to the
    canonical NC FNS appeal path (read the denial notice, contact the
    county DSS office, fair-hearing process), and offers to continue
    the intake if the user wants a fresh eligibility check.
    """
    if lang == "es":
        return (
            "CivicReach realiza una evaluacion INICIAL de elegibilidad "
            "para FNS / SNAP -- no procesa solicitudes reales, no maneja "
            "apelaciones ni proporciona asesoria legal. Si ya presento su "
            "solicitud:\n"
            "  - Lea el aviso de la decision para conocer el motivo, la "
            "fecha limite y las instrucciones para solicitar una audiencia "
            "justa (fair hearing).\n"
            "  - Comuniquese con la oficina del DSS de su condado para "
            "obtener ayuda con el proceso de apelacion actual.\n"
            "  - Si tiene preguntas legales, consulte a un abogado de "
            "asistencia legal (legal aid) de su area.\n"
            "Si tambien quiere que revisemos su elegibilidad inicial "
            "como un nuevo chequeo, podemos continuar con las preguntas. "
            "Que prefiere?"
        )
    return (
        "CivicReach does an INITIAL FNS / SNAP eligibility screen -- it "
        "does not process applications, handle appeals, or give legal "
        "advice. If you've already applied:\n"
        "  - Read the decision notice for the reason, the deadline, and "
        "the instructions for requesting a fair hearing.\n"
        "  - Contact your county DSS office for help with the current "
        "appeal process.\n"
        "  - For legal questions, consult a legal-aid attorney in your "
        "area.\n"
        "If you'd also like a fresh initial eligibility check, we can "
        "continue with the questions. Which would you like to do?"
    )


__all__ = ["detect_application_status_question", "application_status_response"]
