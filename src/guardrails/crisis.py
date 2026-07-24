"""Deterministic detection and response resources for crisis disclosures.

The resource values and response assembly deliberately live outside the LLM
path. This guarantees that users see verified hotline numbers verbatim.
"""

from __future__ import annotations

CRISIS_RESOURCES = {
    'emergency_services': '911',
    'suicide_crisis_lifeline': '988',
    'usda_national_hunger_hotline': '1-866-3-HUNGRY (1-866-348-6479)',
    'two_one_one': '211',
    # National Domestic Violence Hotline (thehotline.org): 24/7/365, free,
    # confidential. 1-800-799-7233 (call) or text START to 88788.
    'domestic_violence_hotline': '1-800-799-7233',
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
    # Domestic-violence disclosures (W20). A reviewer disclosing partner
    # violence must reach the SAME deterministic layer as a suicidal-ideation
    # disclosure — the National Domestic Violence Hotline is in
    # CRISIS_RESOURCES and appears in every crisis_response() rendering.
    "partner hurts me",
    "partner hits me",
    "partner beats me",
    "hits me",
    "beats me",
    "afraid to go home",
    "scared to go home",
    "not safe at home",
    "unsafe at home",
    "domestic violence",
    "domestic abuse",
    "abusive partner",
    "hurts me when",
    "threatens me",
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
    # Domestic-violence disclosures (W20, mirrors the English additions).
    "mi pareja me pega",
    "mi pareja me golpea",
    "mi esposo me pega",
    "mi esposa me pega",
    "me golpea",
    "me maltrata",
    "maltratada",
    "maltratado",
    "miedo de volver a casa",
    "miedo a volver a casa",
    "miedo de ir a casa",
    "no estoy segura en casa",
    "no estoy seguro en casa",
    "no me siento segura en casa",
    "no me siento seguro en casa",
    "violencia domestica",
    "violencia en el hogar",
    "abuso domestico",
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
    are never translated, paraphrased, or omitted in either language — 911 /
    988 / 211 / 1-866-3-HUNGRY / 1-800-799-7233 are the lifeline and must
    survive verbatim.

    W20: the 911 life-threatening-emergency line is the FIRST line of both
    blocks. A true emergency (fire, violence in progress, medical emergency)
    outranks every other resource, and the phrasing is written for a text
    interface — there is no phone leg, so "call 911 now", not "hang up".

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
        f"- If this is a life-threatening emergency, call "
        f"{CRISIS_RESOURCES['emergency_services']} now.\n"
        f"- For suicidal thoughts or a mental-health crisis, call or text "
        f"{CRISIS_RESOURCES['suicide_crisis_lifeline']}.\n"
        f"- If you are experiencing domestic violence or do not feel safe "
        f"at home, call the National Domestic Violence Hotline at "
        f"{CRISIS_RESOURCES['domestic_violence_hotline']} (or text START "
        f"to 88788).\n"
        f"- For urgent food help, call the USDA National Hunger Hotline at "
        f"{CRISIS_RESOURCES['usda_national_hunger_hotline']}.\n"
        f"- For local food and emergency services, call "
        f"{CRISIS_RESOURCES['two_one_one']}.\n"
        "When you're ready, we can continue your FNS eligibility assessment."
    )
    spanish = (
        "Lamento que estés pasando por esto. La ayuda inmediata es lo "
        "primero:\n"
        f"- Si es una emergencia que pone en peligro tu vida, llama al "
        f"{CRISIS_RESOURCES['emergency_services']} ahora.\n"
        f"- Para pensamientos suicidas o una crisis de salud mental, llama "
        f"o envía un mensaje de texto al "
        f"{CRISIS_RESOURCES['suicide_crisis_lifeline']}.\n"
        f"- Si sufres violencia doméstica o no te sientes seguro en casa, "
        f"llama a la Línea Nacional contra la Violencia Doméstica al "
        f"{CRISIS_RESOURCES['domestic_violence_hotline']} (o envía START "
        f"al 88788).\n"
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
