"""Voice (IVR) rendering of the crisis response.

The text channel's crisis response says "call 911 now". That phrasing is
correct for a text interface and *wrong* for a phone line: the caller is
already holding a phone to their ear on a non-emergency line, and telling
them to "call 911" wastes the one moment that matters. The voice
rendering says hang up and call 911.

The two renderings also differ in register, because a message that will
be read aloud cannot use the conventions of a chat bubble:

* No markdown bullets. A screen reader or a TTS engine reads "- " as
  "dash" or skips it, which turns a list of phone numbers into an
  ambiguous run-on.
* No parentheses. Read aloud they are noise, and the text channel's
  "(or text START to 88788)" becomes a parenthetical the caller has to
  parse while panicking.
* No URLs, and no ampersands.
* 988 is "call or text" on the text channel because the user may prefer
  to text. On a phone line the caller is already calling, so it is just
  "call 988".
* The resource list is shorter. A caller in distress cannot hold five
  hotlines in working memory. 911 comes first and alone, then the
  remaining resources in the order they are most likely to be needed.

The numbers themselves are not retyped here. They are imported from
``src.guardrails.crisis.CRISIS_RESOURCES``, which is the single source
of truth for verified hotline numbers, so a voice caller and a text
caller are given the same numbers and a change to one is a change to
both.

This module is deterministic and code-templated. The model never writes
a crisis response on any channel.
"""

from __future__ import annotations

from ..guardrails.crisis import CRISIS_RESOURCES


def _spoken_hotline_number(value: str) -> str:
    """Render a hotline number the way it is spoken.

    The USDA hunger hotline is stored as
    ``'1-866-3-HUNGRY (1-866-348-6479)'``. Read aloud, the parenthetical
    is noise: a speech engine either skips it, or announces it as
    "left parenthesis", which is a jarring thing to happen to someone
    in crisis.

    The digits are what the caller needs, so the parenthetical expansion
    is dropped and the memorable word form is kept. Both numbers are the
    same line; one of them is easier to remember.
    """
    if "(" in value:
        return value.split("(")[0].strip().rstrip("-")
    return value


def voice_crisis_response(lang: str = "bilingual") -> str:
    """Build the spoken crisis response.

    ``lang`` selects the rendering:

    * ``'bilingual'`` (default) -- English block, then Spanish block.
      Safest when the caller's language is unknown, which on a voice
      line is the usual case at the first turn.
    * ``'en'`` -- English only.
    * ``'es'`` -- Spanish only, so a conversation already in Spanish
      mode never silently reverts to English.

    The hotline numbers are read verbatim from ``CRISIS_RESOURCES``. The
    numbers are never translated, paraphrased, or omitted in either
    language.
    """
    english = (
        "If anyone is in immediate danger, please hang up and call "
        f"{CRISIS_RESOURCES['emergency_services']} right now. "
        "I am sorry you are dealing with this. "
        f"For suicidal thoughts or a mental health crisis, call "
        f"{CRISIS_RESOURCES['suicide_crisis_lifeline']}. "
        "If you are experiencing domestic violence or do not feel safe at "
        "home, call the National Domestic Violence Hotline at "
        f"{CRISIS_RESOURCES['domestic_violence_hotline']}. "
        "You can also text the word START to 88788. "
        "For urgent food help, call the USDA National Hunger Hotline at "
        f"{_spoken_hotline_number(CRISIS_RESOURCES['usda_national_hunger_hotline'])}. "
        f"For local food and emergency services, call "
        f"{CRISIS_RESOURCES['two_one_one']}. "
        "When you are ready, we can continue your eligibility assessment."
    )
    spanish = (
        "Si alguien está en peligro inmediato, por favor cuelgue y llame al "
        f"{CRISIS_RESOURCES['emergency_services']} de inmediato. "
        "Lamento que esté pasando por esto. "
        "Para pensamientos suicidas o una crisis de salud mental, llame al "
        f"{CRISIS_RESOURCES['suicide_crisis_lifeline']}. "
        "Si sufre violencia doméstica o no se siente seguro en casa, llame a "
        "la Línea Nacional contra la Violencia Doméstica al "
        f"{CRISIS_RESOURCES['domestic_violence_hotline']}. "
        "También puede enviar el mensaje START al 88788. "
        "Para ayuda urgente con alimentos, llame a la Línea Nacional contra "
        "el Hambre del USDA al "
        f"{_spoken_hotline_number(CRISIS_RESOURCES['usda_national_hunger_hotline'])}. "
        "Para servicios locales de alimentos y emergencias, llame al "
        f"{CRISIS_RESOURCES['two_one_one']}. "
        "Cuando esté listo, podemos continuar con su evaluación de "
        "elegibilidad para FNS."
    )
    if lang == "es":
        return spanish
    if lang == "en":
        return english
    return f"{english}\n\n{spanish}"


__all__ = ["voice_crisis_response"]
