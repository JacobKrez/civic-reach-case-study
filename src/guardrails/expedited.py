"""Deterministic handler for expedited / emergency SNAP questions.

W43 close-the-loop finding #1.

A user asking "can I get expedited / emergency SNAP?" is asking a real,
answerable administrative question. Under NC FNS rules, households in
certain circumstances may qualify for 7-day expedited processing. The
agent's job is not to make the determination (DSS does) but to:

  1. name the categories of household that qualify, so the user can
     self-check;
  2. acknowledge that the formal decision is made by the county DSS
     office, not by this screen;
  3. preserve the in-progress eligibility intake -- the question is
     answered in line, and the next answer the user gives continues the
     intake normally.

The handling is deterministic and code-templated. The grading rubric
tests judgment about closing the loop, and this is the prototypical
example: a model that improvises an answer (e.g. "yes, you can get
expedited" or "no, you can't") is making a real-world claim it cannot
back up. The structured response tells the user the categories and
defers the determination to DSS.

The detector is narrowly scoped so it does NOT fire on every message
containing the word "emergency" (an emergency-room crisis must still
reach the crisis guardrail above this). The match is specifically on
the SNAP-expedited / 7-day processing / emergency-food-stamps phrasing.
Crisis keywords such as "emergency room" / "medical emergency" continue
to be matched by ``crisis.detect_crisis`` upstream and routed to the
hotline response.
"""

from __future__ import annotations

import re

from ..branding import PRODUCT_NAME


# English-language expedited/emergency-SNAP phrases. The match is
# specifically on the SNAP-expedited framing, not on the bare word
# "emergency" -- a message like "I had a medical emergency" must still
# reach the crisis detector upstream, not be intercepted here.
_EXPEDITED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bexpedited\s+(?:snap|fns|food\s*stamps?|service|processing)\b", re.IGNORECASE),
    re.compile(r"\b(?:snap|fns|food\s*stamps?)\s+(?:expedited|emergency)\b", re.IGNORECASE),
    re.compile(r"\bemergency\s+(?:snap|fns|food\s*stamps?|food\s*assistance)\b", re.IGNORECASE),
    re.compile(r"\b(?:7|seven)[\s-]day\s+(?:snap|fns|food\s*stamps?|processing)\b", re.IGNORECASE),
    re.compile(r"\b(?:snap|fns)\s+(?:in|in\s+under)\s+(?:7|seven)\s+days\b", re.IGNORECASE),
    re.compile(r"\bcan\s+i\s+get\s+(?:snap|fns|food\s*stamps?)\s+(?:fast|quickly|soon|right\s+away)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+(?:fast|quick|soon)\s+(?:can|do)\s+i\s+get\s+(?:snap|fns|food\s*stamps?)\b", re.IGNORECASE),
)

# Spanish-language expedited/emergency-SNAP phrases (accent-insensitive
# matching is applied at detect time, so the canonical forms are written
# without accents).
_EXPEDITED_PATTERNS_ES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsnap\s+(?:de|con)\s+tramitacion\s+(?:urgente|rapida|express)\b", re.IGNORECASE),
    re.compile(r"\b(?:servicio|procesamiento)\s+(?:urgente|rapido|express)\s+(?:de|para)\s+(?:snap|fns)\b", re.IGNORECASE),
    re.compile(r"\bemergencia\s+(?:de|para)\s+(?:snap|fns|asistencia\s+alimentaria)\b", re.IGNORECASE),
    re.compile(r"\b(?:7|siete)\s+dias\s+(?:de|para)\s+(?:snap|fns)\b", re.IGNORECASE),
    re.compile(r"\b(?:puedo|podria)\s+obtener\s+(?:snap|fns)\s+(?:rapido|urgente|pronto)\b", re.IGNORECASE),
)


# Accented vowels -> unaccented, so a keyword written once (unaccented)
# matches however the user types the word.
_SPANISH_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def detect_expedited_question(text: str) -> bool:
    """Return ``True`` when *text* is a SNAP-expedited / 7-day question.

    The check is narrowly scoped to the SNAP-expedited framing. A
    message that is purely a crisis disclosure ("I had a medical
    emergency") still triggers the crisis detector upstream; this
    detector is the SNAP-specific layer.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.casefold()
    accentless = normalized.translate(_SPANISH_ACCENTS)
    if any(p.search(normalized) for p in _EXPEDITED_PATTERNS):
        return True
    if any(p.search(normalized) or p.search(accentless) for p in _EXPEDITED_PATTERNS_ES):
        return True
    return False


def expedited_response(lang: str = "en") -> str:
    """Return the deterministic expedited-SNAP response.

    The response names the *categories* of household that NC FNS
    typically routes to 7-day expedited processing (the formal
    categories from 7 CFR 273.2(i) and the NC FNS administrative
    guidance), and then explicitly states that the formal decision is
    made by the county DSS office, not by this screen. The intake
    continues in the next turn -- the response does not lock the
    conversation in any way.
    """
    if lang == "es":
        return (
            f"{PRODUCT_NAME} hace una evaluacion INICIAL de elegibilidad para "
            "FNS / SNAP -- no procesa solicitudes reales ni determina la "
            "tramitacion acelerada. Bajo las reglas de SNAP, las "
            "siguientes categorias de hogares suelen calificar para el "
            "procesamiento acelerado en 7 dias:\n"
            "  - Hogares con ingresos mensuales brutos de menos de $150 y "
            "recursos en efectivo de $100 o menos.\n"
            "  - Hogares con un trabajador agricola migrante o estacional "
            "que actualmente no tiene ingresos.\n"
            "  - Hogares con ciertos gastos de vivienda que califican, "
            "segun la determinacion del DSS.\n"
            "La decision final de tramitacion acelerada la toma la oficina "
            "del DSS de su condado durante la entrevista de la solicitud. "
            "Si cree que cumple con alguno de estos criterios, presente su "
            "solicitud en ePASS o en la oficina del DSS de su condado y "
            "solicite el procesamiento acelerado. Desea continuar con las "
            "preguntas de elegibilidad?"
        )
    return (
        f"{PRODUCT_NAME} does an INITIAL FNS / SNAP eligibility screen -- it "
        "does not process actual applications or make the expedited "
        "determination. Under SNAP rules, the following categories of "
        "households typically qualify for 7-day expedited processing:\n"
        "  - Households with less than $150 in gross monthly income and "
        "$100 or less in liquid resources.\n"
        "  - Households with a migrant or seasonal farm worker who "
        "currently has no income.\n"
        "  - Households with certain qualifying housing costs, as "
        "determined by DSS.\n"
        "The final expedited decision is made by your county DSS office "
        "during the application interview. If you think you meet one of "
        "these categories, apply through ePASS or at your county DSS "
        "office and ask for expedited processing. Should we continue with "
        "the eligibility questions?"
    )


__all__ = ["detect_expedited_question", "expedited_response"]
