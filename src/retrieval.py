"""Small local rule retriever used to ground eligibility explanations."""

from __future__ import annotations

_RULES = {
    "gross": (
        "For a household without an elderly or disabled member, the initial "
        "NC FNS screen applies the gross monthly income standard at 130% of "
        "the federal poverty guideline."
    ),
    "net": (
        "A household with an elderly (age 60+) or disabled member is not "
        "subject to the gross-income test; the initial screen uses the net "
        "monthly income standard at 100% of the federal poverty guideline."
    ),
    "intake": (
        "An initial NC FNS income screen needs household size, gross monthly "
        "income, and whether the household includes an elderly or disabled member."
    ),
}


def retrieve(query: str) -> str:
    """Return the most relevant embedded rule text for a narrow query."""
    normalized = query.lower()
    if "elderly" in normalized or "disabled" in normalized or "net" in normalized:
        return _RULES["net"]
    if "gross" in normalized or "130" in normalized:
        return _RULES["gross"]
    return _RULES["intake"]


__all__ = ["retrieve"]
