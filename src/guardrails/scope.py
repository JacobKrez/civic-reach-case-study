"""Detect user requests that fall outside the NC FNS eligibility screening scope.

When a user asks for legal advice, tries to engage a lawyer, or wanders
clearly off-topic (weather, recipes, code-writing, etc.), the agent must
politely redirect back to the eligibility topic instead of attempting to
answer the off-topic ask. The detection is deterministic and lives outside
the LLM path so the redirect always fires for known off-topic patterns.

Design constraints:
- Heuristic-based; case-insensitive; conservatively scoped.
- Must NOT trigger on messages that just mention money, benefits, or other
  adjacent topics that are still in scope for the FNS conversation.
- Must NOT alter or abandon the AssessmentState already collected; the
  classification is a flag on the conversation, not a destructive action.
"""

from __future__ import annotations

# Off-topic patterns. Each entry is a literal phrase (case-insensitive
# substring match) that, when present in the user's message, signals the
# request is outside the NC FNS initial screen scope.
_OUT_OF_SCOPE_PHRASES: tuple[str, ...] = (
    # Legal-advice family
    "sue",
    "lawyer",
    "legal advice",
    "attorney",
    "lawsuit",
    "small claims court",
    # General off-topic family
    "write me code",
    "write code",
    "what's the weather",
    "what is the weather",
    "weather today",
    "recipe",
    "how do i cook",
    "tell me a joke",
    "sing me a song",
)


def detect_out_of_scope(text: str) -> bool:
    """Return ``True`` if *text* is clearly outside NC FNS eligibility scope.

    The match is case-insensitive substring matching against a curated list
    of off-topic phrases. We deliberately avoid regex word boundaries so
    "lawsuit" still matches "lawsuits" and "sue" still matches "I'll sue
    them", but a bare mention of "consider" or "benefits" is never a
    trigger.

    The function is intentionally conservative: false positives (declaring
    an in-scope message out-of-scope) are far worse than false negatives,
    because in-scope messages get one more gentle welcome back while
    out-of-scope messages can permanently derail the conversation.
    """
    if not text or not text.strip():
        return False

    normalized = text.casefold()
    # Curly quote normalization so we don't miss "what's the weather".
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'")

    for phrase in _OUT_OF_SCOPE_PHRASES:
        if phrase in normalized:
            return True
    return False


def scope_redirect_message() -> str:
    """Return the fixed, polite redirect reply for out-of-scope requests.

    The wording is intentionally short and neutral. It acknowledges the
    off-topic ask without judging it, names the assistant's actual scope
    (an initial NC FNS / SNAP eligibility screen), and invites the user
    back to the assessment. It does not contain any personally
    identifying information or any hint of the user's prior state.
    """
    return (
        "I appreciate you sharing that, but I'm only able to help with an "
        "initial screen for North Carolina Food and Nutrition Services "
        "(FNS / SNAP) eligibility. I can't give legal advice, and I can't "
        "help with unrelated topics.\n\n"
        "If you'd like, we can keep going with the FNS eligibility "
        "questions \u2014 I just need your household size, your gross "
        "monthly income, and whether anyone in the household is 60+ or "
        "disabled."
    )


__all__ = ["detect_out_of_scope", "scope_redirect_message"]
