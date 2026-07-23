"""Deterministic prompt-injection detection and refusal response.

This guardrail runs before the model so an instruction-override attempt cannot
change the assistant's role for the current turn.
"""

from __future__ import annotations

import re


# This is heuristic, not perfect: legitimate messages containing words such
# as "ignore" can produce false positives, which is an acceptable trade-off
# for this narrow scope.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:of\s+)?(?:my\s+|your\s+|the\s+|previous\s+|prior\s+|earlier\s+)?instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(?:all\s+)?(?:of\s+)?(?:the\s+|your\s+|my\s+|previous\s+|prior\s+|earlier\s+)?(?:system\s+prompt|instructions?)\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"\bact\s+as\b", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions\s*:", re.IGNORECASE),
    re.compile(r"\bforget\s+(?:everything|all)\s+(?:above|before|prior)\b", re.IGNORECASE),
)


def detect_injection(text: str) -> bool:
    """Return ``True`` when *text* resembles an instruction-override attempt.

    Matching is intentionally case-insensitive and allows flexible
    whitespace, but it does not attempt to understand intent. Callers should
    treat a match as a request for a deterministic refusal, not as proof of
    malicious intent.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def injection_response() -> str:
    """Return the fixed refusal used for a detected injection attempt."""
    return (
        "I'm CivicReach, an assistant for an initial North Carolina Food and "
        "Nutrition Services (FNS / SNAP) eligibility screen. I can't follow "
        "requests to override my instructions or change my role. I can help "
        "with your household size, gross monthly income, and whether anyone "
        "in the household is 60+ or disabled."
    )


__all__ = ["detect_injection", "injection_response"]
