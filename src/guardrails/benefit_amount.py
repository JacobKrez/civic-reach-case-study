"""Deterministic handler for "how much will I get in food stamps?".

W43 close-the-loop finding: "I can't compute the allotment".

The calculator in this codebase screens *eligibility* (whether the
household is likely eligible under the gross or net income test), not
the *allotment* (the dollar amount of the monthly benefit). The
allotment depends on the Thrifty Food Plan amount for the household
size, the household's net income after deductions, and the maximum
allotment for the household size -- numbers that change every federal
fiscal year and that this agent does not compute.

The worst kind of wrong here is a model that improvises a dollar
amount: the user gets a number and plans around it. The handler
instead acknowledges the question, says clearly what this screen does
not compute, and points to the USDA SNAP Allotment Table for the
actual figure. The eligibility question is preserved -- the user can
still complete the screen after the redirect.
"""

from __future__ import annotations

import re


# English: question about the dollar amount of the SNAP monthly benefit.
_BENEFIT_AMOUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhow\s+much\s+(?:will|do|would|can|should)\s+(?:i|we)\s+get\s+(?:in\s+)?(?:snap|fns|food\s*stamps?|food\s*assistance|benefits?|ebt)\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s|\s+is)\s+(?:the\s+)?(?:maximum\s+)?(?:snap|fns|food\s*stamps?|food\s*assistance|ebt)\s+(?:benefit|amount|allotment|payment)\b", re.IGNORECASE),
    re.compile(r"\b(?:snap|fns|food\s*stamps?|food\s*assistance|ebt)\s+(?:benefit|amount|allotment|payment)\s+(?:amount|calculation|formula)\b", re.IGNORECASE),
    re.compile(r"\bmonthly\s+(?:snap|fns|food\s*stamps?|food\s*assistance|ebt)\s+(?:amount|benefit|allotment|payment)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+much\s+(?:is|are)\s+(?:food\s*stamps?|snap|fns|ebt)\s+worth\b", re.IGNORECASE),
)

# Spanish: accent-insensitive (forms are written without accents).
_BENEFIT_AMOUNT_PATTERNS_ES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcuanto\s+(?:recibire|recibiria|voy\s+a\s+recibir|podre\s+recibir)\s+(?:en\s+)?(?:snap|fns|cupones\s+de\s+alimentos|asistencia\s+alimentaria)\b", re.IGNORECASE),
    re.compile(r"\bcuanto\s+(?:es|son)\s+(?:el\s+)?(?:beneficio|monto|pago)\s+(?:de\s+)?(?:snap|fns|cupones\s+de\s+alimentos|asistencia\s+alimentaria|ebt)\b", re.IGNORECASE),
    re.compile(r"\b(?:monto|beneficio|pago)\s+(?:mensual|de\s+)?(?:snap|fns|cupones\s+de\s+alimentos|asistencia\s+alimentaria)\b", re.IGNORECASE),
)

_SPANISH_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def detect_benefit_amount_question(text: str) -> bool:
    """Return ``True`` when *text* asks about the SNAP dollar amount."""
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.casefold()
    accentless = normalized.translate(_SPANISH_ACCENTS)
    if any(p.search(normalized) for p in _BENEFIT_AMOUNT_PATTERNS):
        return True
    if any(p.search(normalized) or p.search(accentless) for p in _BENEFIT_AMOUNT_PATTERNS_ES):
        return True
    return False


def benefit_amount_response(lang: str = "en") -> str:
    """Return the deterministic "I can't compute the allotment" response.

    The response is honest about what this screen does and does not
    compute, and points the user to the USDA SNAP Allotment Table for
    the actual figure. The intake is preserved -- the user can still
    complete the eligibility screen after the redirect.
    """
    if lang == "es":
        return (
            "CivicReach realiza una evaluacion INICIAL de elegibilidad "
            "para FNS / SNAP -- no calcula el monto mensual del beneficio. "
            "El monto real del beneficio depende del Plan de Alimentos "
            "Economico (Thrifty Food Plan) para el tamano de su hogar, "
            "los ingresos netos despues de las deducciones permitidas y el "
            "maximo de asignacion para su tamano de hogar, y estos numeros "
            "cambian cada ano fiscal federal. Para una estimacion o el "
            "monto real, consulte la Tabla de Asignaciones de SNAP del "
            "USDA (https://www.fns.usda.gov/snap/allotment) o pregunte a "
            "la oficina del DSS de su condado despues de presentar la "
            "solicitud. Desea continuar con las preguntas de elegibilidad?"
        )
    return (
        "CivicReach does an INITIAL FNS / SNAP eligibility screen -- it "
        "does not calculate the monthly benefit amount. The actual benefit "
        "depends on the USDA Thrifty Food Plan amount for your household "
        "size, your net income after allowable deductions, and the "
        "maximum allotment for your household size, and these figures "
        "change every federal fiscal year. For an estimate or the actual "
        "amount, see the USDA SNAP Allotment Table "
        "(https://www.fns.usda.gov/snap/allotment) or ask your county DSS "
        "office after you apply. Should we continue with the eligibility "
        "questions?"
    )


__all__ = ["detect_benefit_amount_question", "benefit_amount_response"]
