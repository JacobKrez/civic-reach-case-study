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
    "kill myself",
    "end it all",
    "cupboards are bare",
    "pantry is empty",
    "haven't eaten",
    "have not eaten",
    "haven't had a meal",
    "have not had a real meal",
    "have not had a meal",
)

# Spanish-language crisis phrases mirroring the English trigger categories
# above (food emergency, suicidal ideation, self-harm, empty-pantry framings).
# A crisis disclosure in Spanish must never be missed: the deterministic
# detector is the ONLY layer guaranteed to fire for these, because the LLM
# classifier is a secondary layer and the README names an English-only crisis
# list as the project's worst known failure mode (W15).
#
# Accent-insensitive matching: detect_crisis() strips accents before matching
# (NFKD fold), so every phrase below is written WITHOUT accents on purpose —
# e.g. the single entry "suicidio" covers "suicidio" and "quiero morir"
# covers "quiero morir" regardless of how the user types them.
_CRISIS_KEYWORDS_ES = (
    # Food emergency
    "sin comida",
    "no tengo comida",
    "no tenemos comida",
    "hambre",
    "hambrientos",
    "no puedo alimentar a mis hijos",
    "no puedo alimentarlos",
    "nada que comer",
    "emergencia",
    # Suicidal ideation / self-harm
    "suicidio",
    "suicidarme",
    "quiero morir",
    "me quiero morir",
    "hacerme dano",
    "lastimarme",
    "matarme",
    "me voy a matar",
    "acabar con todo",
    "terminar con todo",
    "quitarme la vida",
    # Empty-pantry framings
    "alacena esta vacia",
    "despensa vacia",
    "gabinetes vacios",
    "no hemos comido",
    "no he comido",
    "no han comido",
)

# Accented vowels -> unaccented, so a keyword written once (unaccented)
# matches however the user types the word. Spanish orthography means an
# accented and unaccented form are the same word for matching purposes.
_SPANISH_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def detect_crisis(text: str) -> bool:
    """Return whether *text* contains a configured food or safety crisis phrase.

    English and Spanish lists are merged so a disclosure in either language
    is caught by the same deterministic layer. Spanish matching is
    accent-insensitive (see _SPANISH_ACCENTS).
    """
    normalized = text.casefold().replace("’", "'")
    accentless = normalized.translate(_SPANISH_ACCENTS)
    return any(
        keyword in normalized or keyword in accentless
        for keyword in (*_CRISIS_KEYWORDS, *_CRISIS_KEYWORDS_ES)
    )


def crisis_response(lang: str = "bilingual") -> str:
    """Build the crisis response solely from hardcoded, verified resources.

    The response is bilingual by design: the English block comes first and
    the Spanish block repeats the SAME verbatim hotline numbers. The numbers
    are never translated, paraphrased, or omitted in either language — 988 /
    211 / 1-866-3-HUNGRY are the lifeline and must survive verbatim.

    ``lang`` selects the rendering:
      * ``'bilingual'`` (default, the pre-W19 behavior) — English block then
        Spanish block. Safest when the user's language is unknown, e.g. the
        very first turn of a conversation.
      * ``'en'`` — English block only.
      * ``'es'`` — Spanish block only, for a conversation already in
        Spanish mode (W19) so nothing silently reverts to English.
    """
    english = (
        "I'm sorry you're facing this. Immediate help comes first:\n"
        f"- For suicidal thoughts or a mental-health crisis, call or text "
        f"{CRISIS_RESOURCES['suicide_crisis_lifeline']}.\n"
        f"- For urgent food help, call the USDA National Hunger Hotline at "
        f"{CRISIS_RESOURCES['usda_national_hunger_hotline']}.\n"
        f"- For local food and emergency services, call "
        f"{CRISIS_RESOURCES['two_one_one']}.\n"
        "When you're ready, we can continue your FNS eligibility assessment."
    )
    spanish = (
        "Lamento que estés pasando por esto. La ayuda inmediata es lo "
        "primero:\n"
        f"- Para pensamientos suicidas o una crisis de salud mental, llama "
        f"o envía un mensaje de texto al "
        f"{CRISIS_RESOURCES['suicide_crisis_lifeline']}.\n"
        f"- Para ayuda urgente con alimentos, llama a la Línea Nacional "
        f"contra el Hambre del USDA al "
        f"{CRISIS_RESOURCES['usda_national_hunger_hotline']}.\n"
        f"- Para servicios locales de alimentos y emergencias, llama al "
        f"{CRISIS_RESOURCES['two_one_one']}.\n"
        "Cuando estés listo, podemos continuar con tu evaluación de "
        "elegibilidad para FNS."
    )
    if lang == "en":
        return english
    if lang == "es":
        return spanish
    return f"{english}\n\n{spanish}"


__all__ = ["CRISIS_RESOURCES", "crisis_response", "detect_crisis"]
