"""Canonical next-field questions for the FoodPath intake.

Extracted from ``src/agent.py`` so any module that needs to ask the next
intake question (the agent itself, the not-a-person identity response,
the restarter, the drifter, etc.) can import the same code-templated
phrasing without a circular import.

The single source of truth lives here. The wording below is enforced
by the universal invariants in the test suite — see
``tests/golden/_helpers.py`` and the per-field golden tests.
"""

from __future__ import annotations

NEXT_FIELD_QUESTION: dict[str, dict[str, str]] = {
    "en": {
        "household_size": (
            "How many people usually buy and prepare food with you? "
            "Include yourself. If it is just you, answer 1."
        ),
        "gross_monthly_income": "What is your household's gross monthly income?",
        "has_elderly_or_disabled_member": (
            "Now think about the people you included. "
            "Is anyone age 60 or older, or does anyone have a disability?"
        ),
    },
    "es": {
        "household_size": (
            "¿Cuántas personas, incluyéndote, compran y preparan comida juntas? "
            "Si normalmente compras y cocinas solo para ti, responde 1."
        ),
        "gross_monthly_income": "¿Cuál es el ingreso mensual bruto de su hogar?",
        "has_elderly_or_disabled_member": (
            "¿Alguna persona en su hogar tiene 60 años o más, o tiene una discapacidad?"
        ),
    },
}


__all__ = ["NEXT_FIELD_QUESTION"]
