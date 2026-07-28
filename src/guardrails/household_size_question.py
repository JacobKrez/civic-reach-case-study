"""Deterministic handler for "what's the max household size?".

Live human review, 2026-07-28: asked whether there's a maximum household
size, the model improvised "no max... tops out around 8 to 10 people" --
a fabricated number with no grounding. The calculator's precomputed
income-limit table only has explicit rows through household size 8
(``_LIMITS`` in ``src/calculator.py``); the model most likely echoed that
implementation detail as if it were a real program cap. It is not: SNAP
itself has no statutory maximum household size, and this calculator
extrapolates the gross/net income limits linearly for any household size
above 8 (``_limit_for``'s ``household_size - 8`` branch). The one real
constraint is operational: this screener's own intake field caps
household size at ``calculator.max_household_size()`` (20) as a
practical upper bound for a self-service form, not a SNAP eligibility
rule.

The worst kind of wrong here is the same class as the benefit-amount
hallucination this codebase already guards against (see
``benefit_amount.py``): a model inventing a plausible-sounding number
that the user has no way to know is fabricated. The handler instead
states the actual screener limit -- sourced from
``calculator.max_household_size()`` so it can never drift from the real
schema cap -- and is honest that SNAP itself does not cap household
size, pointing anyone above the screener's limit to their county DSS
office directly.
"""

from __future__ import annotations

import re

from ..branding import PRODUCT_NAME
from ..calculator import max_household_size

_MAX_HOUSEHOLD_SIZE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:what'?s|what\s+is|is\s+there)\s+(?:the\s+)?(?:max(?:imum)?|"
        r"biggest|largest|highest|limit\s+(?:on|for|to))\b[^.\n?]{0,40}"
        r"\b(?:household|family)\s*(?:size)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:max(?:imum)?|biggest|largest|highest)\s+(?:household|"
        r"family)\s*(?:size)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhow\s+many\s+people\s+can\s+(?:be\s+in|i\s+(?:put|list|include)"
        r"\s+in)\s+(?:my|a|the)\s+household\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:does|is\s+there)\s+(?:a\s+)?(?:cap|limit)\s+on\s+household"
        r"\s*(?:size)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhousehold\s*size\s+(?:have\s+)?(?:a\s+)?(?:cap|limit|maximum)\b",
        re.IGNORECASE,
    ),
)

_MAX_HOUSEHOLD_SIZE_PATTERNS_ES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:cual\s+es\s+el\s+)?(?:tamano|numero)\s+maximo\s+(?:de\s+)?"
        r"(?:hogar|personas|familia)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhay\s+(?:un\s+)?(?:limite|maximo)\s+(?:de\s+)?(?:personas|"
        r"tamano\s+de\s+hogar)\b",
        re.IGNORECASE,
    ),
)

_SPANISH_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def detect_max_household_size_question(text: str) -> bool:
    """Return ``True`` when *text* asks about a maximum/cap on household size."""
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.casefold()
    accentless = normalized.translate(_SPANISH_ACCENTS)
    if any(p.search(normalized) for p in _MAX_HOUSEHOLD_SIZE_PATTERNS):
        return True
    if any(
        p.search(normalized) or p.search(accentless)
        for p in _MAX_HOUSEHOLD_SIZE_PATTERNS_ES
    ):
        return True
    return False


def max_household_size_response(lang: str = "en") -> str:
    """Return the deterministic, code-grounded max-household-size answer.

    The number is read from ``calculator.max_household_size()`` so this
    reply can never drift from the field's actual schema cap.
    """
    cap = max_household_size()
    if lang == "es":
        return (
            f"{PRODUCT_NAME} puede evaluar hogares de hasta {cap} personas "
            "en una sola solicitud -- ese es un limite practico de este "
            "formulario, no una regla de SNAP/FNS en si. SNAP no tiene un "
            f"tamano maximo de hogar. Si su hogar tiene mas de {cap} "
            "personas, la oficina del DSS de su condado puede ayudarle "
            "directamente."
        )
    return (
        f"{PRODUCT_NAME} can screen a household of up to {cap} people in "
        "one pass -- that's a practical limit of this form, not a SNAP/FNS "
        f"rule itself. SNAP has no maximum household size. If your "
        f"household has more than {cap} people, your county DSS office can "
        "help you directly."
    )


__all__ = ["detect_max_household_size_question", "max_household_size_response"]
