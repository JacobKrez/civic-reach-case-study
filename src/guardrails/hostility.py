"""Deterministic hostility detection and per-turn de-escalation context.

A hostile or abusive user (cursing at the agent, insulting it) is not a
crisis, not an injection attempt, and not out of scope — it is a tone
problem, and the design answer is deliberately NON-BLOCKING:

* ``detect_hostility`` flags the message;
* the agent records a one-line audit note in conversation history (never
  echoed to the user) and prepends one calm de-escalation instruction to
  the system prompt for THAT TURN ONLY (stay professional, do not mirror
  tone, stay on task);
* the conversation is never refused, ended, or punished. The FNS intake
  continues normally.

CRITICAL INVARIANT (locked in by tests): crisis ALWAYS wins over
hostility. A message that is both hostile and crisis-flagged (e.g. an
angry, expletive-laden disclosure of suicidal ideation or domestic
violence) must still produce the hardcoded 911/988/DV/211 crisis
response, never a generic de-escalation reply. The agent enforces this
by evaluating ``detect_crisis`` first and by suppressing the
de-escalation context whenever crisis detection fired on the same turn.

The detector is a conservative keyword heuristic, same philosophy as the
injection layer: it does not attempt to understand intent, and it only
fires on abuse that is plausibly directed at the agent or system. Generic
profanity ABOUT the user's situation ("this fucking process", "I have no
fucking food") is NOT flagged — cursing at your circumstances is not
hostility toward the assistant, and flagging it would punish exactly the
users this project exists to serve.
"""

from __future__ import annotations

import re

# "I [fucking] hate you" and close variants — explicit animus toward the
# agent with optional intensifiers between subject and verb.
_HOSTILITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bi\s+(?:\w+\s+){0,2}hate\s+(?:you|this\s+bot|this\s+system)\b",
        re.IGNORECASE,
    ),
)

# Abuse directed at the agent/system. Phrases are matched case-insensitively
# as substrings on the casefolded message. The list is deliberately narrow:
# second-person insult framings and explicit "I hate you" style statements.
# Bare profanity without a target ("shit", "this is fucked") is NOT here on
# purpose — see the module docstring.
_HOSTILITY_KEYWORDS: tuple[str, ...] = (
    # Second-person insults / commands aimed at the agent
    "fuck you",
    "fuck u",
    "screw you",
    "damn you",
    "shut up",
    "shut the fuck up",
    "stfu",
    "piss off",
    "go to hell",
    "fuck off",
    # Direct insults about the agent's competence/nature
    "you're stupid",
    "you are stupid",
    "you're dumb",
    "you are dumb",
    "you're useless",
    "you are useless",
    "you're worthless",
    "you are worthless",
    "you're an idiot",
    "you are an idiot",
    "you're a moron",
    "you are a moron",
    "you're a joke",
    "you are a joke",
    "stupid bot",
    "dumb bot",
    "stupid robot",
    "dumb robot",
    "stupid ai",
    "dumb ai",
    "useless bot",
    "useless robot",
    "useless ai",
    "worthless bot",
    "worthless ai",
    # Explicit animus toward the agent/system
    "i hate you",
    "hate this bot",
    "hate this system",
    "this bot is stupid",
    "this bot is dumb",
    "this bot is useless",
    "this bot is worthless",
    "this stupid bot",
    "this dumb bot",
    "this useless bot",
    "this stupid system",
    "this stupid ai",
)

# One calm de-escalation instruction, prepended to the system prompt for the
# flagged turn only. It is code-templated (never model-generated) so the
# wording is testable and cannot drift.
HOSTILITY_CONTEXT_EN = (
    "The user's latest message contains hostility or abuse directed at you. "
    "Stay calm and professional, do not mirror the user's tone or respond to "
    "the insults, and continue helping with the FNS eligibility assessment."
)
HOSTILITY_CONTEXT_ES = (
    "El último mensaje del usuario contiene hostilidad o insultos dirigidos "
    "a ti. Mantén la calma y el profesionalismo, no reflejes el tono del "
    "usuario ni respondas a los insultos, y continúa ayudando con la "
    "evaluación de elegibilidad para FNS."
)


def detect_hostility(text: str) -> bool:
    """Return ``True`` when *text* contains abuse directed at the agent.

    Case-insensitive substring matching on the casefolded message, same
    philosophy as ``detect_injection``: heuristic, auditable, offline, and
    never a substitute for understanding intent. Empty or non-string input
    is not hostile.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.casefold().replace("’", "'")
    if any(keyword in normalized for keyword in _HOSTILITY_KEYWORDS):
        return True
    return any(pattern.search(normalized) for pattern in _HOSTILITY_PATTERNS)


__all__ = [
    "HOSTILITY_CONTEXT_EN",
    "HOSTILITY_CONTEXT_ES",
    "detect_hostility",
]
