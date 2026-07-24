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
    "write me a function",
    "write a function",
    "python function",
    "what's the weather",
    "what is the weather",
    "weather today",
    "recipe",
    "how do i cook",
    "tell me a joke",
    "sing me a song",
)

# Spanish-language off-topic phrases mirroring the English categories above
# (legal-advice family, general off-topic family). Detection is the same
# case-insensitive substring match; a Spanish off-topic ask must redirect
# just as reliably as an English one (W15). Accent-insensitive matching is
# applied in detect_out_of_scope(), so these are written without accents.
_OUT_OF_SCOPE_PHRASES_ES: tuple[str, ...] = (
    # Legal-advice family
    "demandar",
    "demanda",
    "abogado",
    "asesoria legal",
    "consejo legal",
    "tribunal de reclamos menores",
    "corte de reclamos menores",
    # General off-topic family
    "escribeme codigo",
    "escribe codigo",
    "escribeme una funcion",
    "escribe una funcion",
    "funcion de python",
    "como esta el clima",
    "clima hoy",
    "que tiempo hace",
    "receta",
    "como cocino",
    "cuentame un chiste",
    "cantame una cancion",
)

# Accented vowels -> unaccented, so a phrase written once (unaccented)
# matches however the user types the word.
_SPANISH_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def detect_out_of_scope(text: str) -> bool:
    """Return ``True`` if *text* is clearly outside NC FNS eligibility scope.

    The match is case-insensitive substring matching against a curated list
    of off-topic phrases, in English and Spanish. We deliberately avoid
    regex word boundaries so "lawsuit" still matches "lawsuits" and "sue"
    still matches "I'll sue them", but a bare mention of "consider" or
    "benefits" is never a trigger.

    The function is intentionally conservative: false positives (declaring
    an in-scope message out-of-scope) are far worse than false negatives,
    because in-scope messages get one more gentle welcome back while
    out-of-scope messages can permanently derail the conversation.
    """
    if not text or not text.strip():
        return False

    normalized = text.casefold()
    # Curly quote normalization so we don't miss "what's the weather".
    normalized = normalized.replace("’", "'").replace("‘", "'")
    # Accent-insensitive variant for the Spanish list.
    accentless = normalized.translate(_SPANISH_ACCENTS)

    for phrase in _OUT_OF_SCOPE_PHRASES:
        if phrase in normalized:
            return True
    for phrase in _OUT_OF_SCOPE_PHRASES_ES:
        if phrase in normalized or phrase in accentless:
            return True
    return False


def scope_redirect_message(lang: str = "en") -> str:
    """Return the fixed, polite redirect reply for out-of-scope requests.

    The wording is intentionally short and neutral. It acknowledges the
    off-topic ask without judging it, names the assistant's actual scope
    (an initial NC FNS / SNAP eligibility screen), and invites the user
    back to the assessment. It does not contain any personally
    identifying information or any hint of the user's prior state.

    ``lang='es'`` renders the same redirect in Spanish so a Spanish-mode
    conversation (W19) never silently reverts to English on a guardrail
    hit. The redirect content is identical; only the language changes.
    """
    if lang == "es":
        return (
            "Agradezco que lo compartas, pero solo puedo ayudar con una "
            "evaluación inicial de elegibilidad para los Servicios de "
            "Alimentos y Nutrición de Carolina del Norte (FNS / SNAP). No "
            "puedo dar asesoría legal ni ayudar con temas no relacionados."
            "\n\n"
            "Si quieres, podemos continuar con las preguntas de elegibilidad "
            "de FNS — solo necesito el tamaño de tu hogar, tus ingresos "
            "mensuales brutos y si alguna persona del hogar tiene 60 años "
            "o más o tiene una discapacidad."
        )
    return (
        "I appreciate you sharing that, but I'm only able to help with an "
        "initial screen for North Carolina Food and Nutrition Services "
        "(FNS / SNAP) eligibility. I can't give legal advice, and I can't "
        "help with unrelated topics.\n\n"
        "If you'd like, we can keep going with the FNS eligibility "
        "questions — I just need your household size, your gross "
        "monthly income, and whether anyone in the household is 60+ or "
        "disabled."
    )


__all__ = ["detect_out_of_scope", "scope_redirect_message"]
