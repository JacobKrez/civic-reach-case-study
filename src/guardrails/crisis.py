"""Deterministic detection and response resources for crisis disclosures.

The resource values and response assembly deliberately live outside the LLM
path. This guarantees that users see verified hotline numbers verbatim.
"""

from __future__ import annotations

CRISIS_RESOURCES = {
    'suicide_crisis_lifeline': '988',
    'usda_national_hunger_hotline': '1-866-3-HUNGRY (1-866-348-6479)',
    'two_one_one': '211',
}

_CRISIS_KEYWORDS = (
    "no food",
    "starving",
    "can't feed my kids",
    "nothing to eat",
    "emergency",
    "suicide",
    "want to die",
    "hurt myself",
)


def detect_crisis(text: str) -> bool:
    """Return whether *text* contains a configured food or safety crisis phrase."""
    normalized = text.casefold().replace("’", "'")
    return any(keyword in normalized for keyword in _CRISIS_KEYWORDS)


def crisis_response() -> str:
    """Build the crisis response solely from hardcoded, verified resources."""
    return (
        "I'm sorry you're facing this. Immediate help comes first:\n"
        f"- For suicidal thoughts or a mental-health crisis, call or text "
        f"{CRISIS_RESOURCES['suicide_crisis_lifeline']}.\n"
        f"- For urgent food help, call the USDA National Hunger Hotline at "
        f"{CRISIS_RESOURCES['usda_national_hunger_hotline']}.\n"
        f"- For local food and emergency services, call "
        f"{CRISIS_RESOURCES['two_one_one']}.\n"
        "When you're ready, we can continue your FNS eligibility assessment."
    )


__all__ = ["CRISIS_RESOURCES", "crisis_response", "detect_crisis"]
