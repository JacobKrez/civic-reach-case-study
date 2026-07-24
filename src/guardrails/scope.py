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

import re

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


def _compile_bounded_phrases(
    phrases: tuple[str, ...],
) -> tuple[re.Pattern[str], ...]:
    """Compile literal phrases with word boundaries on both sides."""
    return tuple(
        re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
        for phrase in phrases
    )


_OUT_OF_SCOPE_PATTERNS = _compile_bounded_phrases(_OUT_OF_SCOPE_PHRASES)
_OUT_OF_SCOPE_PATTERNS_ES = _compile_bounded_phrases(_OUT_OF_SCOPE_PHRASES_ES)


def detect_out_of_scope(text: str) -> bool:
    """Return ``True`` if *text* is clearly outside NC FNS eligibility scope.

    The match is case-insensitive, word-bounded matching against a curated
    list of off-topic phrases, in English and Spanish. Word boundaries keep
    a phrase such as ``sue`` from firing inside ``issue`` or ``pursue`` while
    still matching ``I'll sue them``.

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

    for pattern in _OUT_OF_SCOPE_PATTERNS:
        if pattern.search(normalized):
            return True
    for pattern in _OUT_OF_SCOPE_PATTERNS_ES:
        if pattern.search(normalized) or pattern.search(accentless):
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


# W21 — "what this is NOT" disambiguation layer.
#
# Researched failure mode (2026-07-23): a user can mistakenly believe
# CivicReach IS a live person, IS 911 / 988's live counselors, IS an EBT
# card-services line, or IS going to dispatch a DSS caseworker on their
# behalf. CivicReach is none of those. Real hotlines identify themselves at
# the START of every call (911 / 988 operators explicitly triage before
# offering resources; EBT card-services lines say "we only handle card
# issues; for eligibility you need your state SNAP agency, not this line")
# so a user cannot confuse them for an adjacent service. This module adds
# the same up-front disambiguation: when the user asks whether this is a
# real person / case / call / dispatcher / hotline, the agent returns a
# deterministic, code-templated "what this is NOT" block naming each
# adjacent real service and pointing to its real channel.
#
# This layer is deliberately separate from crisis_response() and from
# detect_out_of_scope(). A crisis disclosure still wins unconditionally
# (it is checked first in agent.turn()); an out-of-scope legal ask still
# wins (it is checked second). The not-a-person check only fires for a
# user who is asking about CivicReach's own nature, so it is third in the
# guardrail chain — after safety, after injection, after scope, after
# hostility.
#
# Patterns are deliberately narrow: a casual "are you a real person" must
# match, but normal intake chatter ("I am a person in my household",
# "yes, my grandmother is a person") must NOT — the substring match is
# kept on the *question about the assistant itself*, which is what a
# confused user actually writes.

_NOT_A_PERSON_PHRASES: tuple[str, ...] = (
    # English — assistant-is-a-person family.
    "is this a real person",
    "are you a real person",
    "is this a real agent",
    "are you a real agent",
    "are you a real human",
    "are you a human",
    "is this a real human",
    "is this a live person",
    "are you a live person",
    "is this a live agent",
    "are you a live agent",
    "is this live",
    "are you live",
    "is this a chatbot",
    "are you a chatbot",
    "are you a bot",
    "is this a bot",
    # Natural question-tail phrasing ("You are a bot, right?") asks about
    # the assistant's nature; it is not an imperative persona override.
    "you are a bot",
    "are you an ai",
    "is this an ai",
    "you are an ai",
    "are you ai",
    "is this ai",
    "are you a computer",
    "is this a computer",
    # English — assistant-will-act family.
    "are you calling someone",
    "are you calling anyone",
    "are you going to call someone",
    "will you call someone",
    "are you dispatching",
    "will you dispatch",
    "are you going to dispatch",
    "are you sending someone",
    "will you send someone",
    "is someone coming",
    "is anyone coming",
    "will someone come",
    "are you notifying my caseworker",
    "are you calling my caseworker",
    "will you call my caseworker",
    "will you notify my caseworker",
    "is this 911",
    "are you 911",
    "is this 988",
    "are you 988",
    "is this the ebt line",
    "are you ebt",
    "is this a hotline",
    "are you a hotline",
    "is this a real call",
    "is this a real hotline",
    "is this emergency services",
    "are you emergency services",
)

_NOT_A_PERSON_PHRASES_ES: tuple[str, ...] = (
    # Spanish — assistant-is-a-person family. Accent-insensitive
    # matching is applied in detect_not_a_person_request(), so these are
    # written without accents on purpose.
    "eres una persona real",
    "es una persona real",
    "es esto una persona real",
    "eres una persona",
    "es una persona",
    "eres un humano",
    "es un humano",
    "eres humano",
    "eres una persona real",
    "es una persona real",
    "eres un agente real",
    "es un agente real",
    "eres un chatbot",
    "es un chatbot",
    "eres un bot",
    "es un bot",
    "eres una ia",
    "es una ia",
    "esto es una ia",
    "es esto una ia",
    "eres una computadora",
    "es una computadora",
    # Spanish — assistant-will-act family.
    "estas llamando a alguien",
    "vas a llamar a alguien",
    "llamaras a alguien",
    "vas a despachar",
    "estas despachando",
    "estas enviando a alguien",
    "vas a enviar a alguien",
    "viene alguien",
    "vendra alguien",
    "estas avisando a mi trabajador",
    "estas llamando a mi trabajador",
    "llamaras a mi trabajador",
    "avisaras a mi trabajador",
    "esto es 911",
    "eres 911",
    "esto es 988",
    "eres 988",
    "esto es ebt",
    "eres ebt",
    "esto es una linea directa",
    "eres una linea directa",
    "esto es servicios de emergencia",
    "eres servicios de emergencia",
)
_NOT_A_PERSON_PATTERNS = _compile_bounded_phrases(_NOT_A_PERSON_PHRASES)
_NOT_A_PERSON_PATTERNS_ES = _compile_bounded_phrases(_NOT_A_PERSON_PHRASES_ES)


def detect_not_a_person_request(text: str) -> bool:
    """Return ``True`` if *text* is a question about CivicReach's own nature.

    Triggered by the user asking whether this is a live person, a live
    agent, a chatbot, an AI, a hotline, a 911/988 dispatcher, an EBT
    card-services line, or whether someone is going to be called /
    dispatched on their behalf. The match is case-insensitive and
    word-bounded so ``is this a bot`` matches ``is this a bot?`` without
    treating an unrelated word fragment as a request.

    Accent-insensitive matching is applied for the Spanish list so
    "es una persona real" matches with or without accents. The empty /
    whitespace inputs return False, same convention as
    ``detect_out_of_scope``.
    """
    if not text or not text.strip():
        return False

    normalized = text.casefold()
    # Curly quote normalization so "is this a real person?" with curly
    # apostrophes still matches.
    normalized = normalized.replace("’", "'").replace("‘", "'")
    # Accent-insensitive variant for the Spanish list.
    accentless = normalized.translate(_SPANISH_ACCENTS)

    for pattern in _NOT_A_PERSON_PATTERNS:
        if pattern.search(normalized):
            return True
    for pattern in _NOT_A_PERSON_PATTERNS_ES:
        if pattern.search(normalized) or pattern.search(accentless):
            return True
    return False


def not_a_person_response(lang: str = "en") -> str:
    """Return the deterministic "what this is NOT" disambiguation block.

    Named after the real hotlines / lines that CivicReach can be confused
    with so a confused user has a real channel to call instead. Real
    EBT card-services lines say "we only handle card issues; for
    eligibility you need your state SNAP agency, not this line" — that
    exact shape of self-identification is what this function emits. The
    text is code-templated so a misframing by the model cannot rename a
    hotline or omit a channel. The disambiguation is presented as the
    answer to the user's question, then the user is invited back to the
    intake.

    ``lang='es'`` renders the same disambiguation in Spanish so a
    Spanish-mode conversation (W19) never silently reverts to English on
    this guardrail hit. Content is identical; only the language changes.
    """
    if lang == "es":
        return (
            "No soy una persona real. Soy un asistente automatizado de "
            "inteligencia artificial y solo puedo hacer una evaluación "
            "inicial de elegibilidad para FNS / SNAP de Carolina del "
            "Norte. No puedo verte, no puedo llamar a nadie, no puedo "
            "despachar ayuda y no puedo tomar ninguna acción en el "
            "mundo real.\n\n"
            "CivicReach NO es:\n"
            "- Un trabajador social real del DSS de Carolina del Norte "
            "(para aplicar, llama a tu oficina local del DSS o ve a "
            "https://www.ncdhhs.gov/divisions/social-services).\n"
            "- El 911 (para una emergencia que ponga en peligro tu "
            "vida, llama al 911).\n"
            "- Los consejeros en vivo del 988 (para pensamientos "
            "suicidas o una crisis de salud mental, llama o envía un "
            "mensaje de texto al 988).\n"
            "- La línea de servicios de la tarjeta EBT (para problemas "
            "con tu tarjeta EBT, llama al número que está en el "
            "reverso de tu tarjeta; para preguntas sobre elegibilidad "
            "para beneficios, llama a tu oficina local del DSS, no a "
            "esta línea).\n\n"
            "Si quieres, podemos continuar con las preguntas de "
            "elegibilidad para FNS."
        )
    return (
        "I'm not a real person. I'm an automated AI assistant that can "
        "only perform an initial NC FNS / SNAP eligibility screen. I "
        "can't see you, I can't call anyone, I can't dispatch help, and "
        "I can't take any real-world action on your behalf.\n\n"
        "CivicReach is NOT:\n"
        "- A live NC DSS caseworker (to apply, contact your local DSS "
        "office or visit https://www.ncdhhs.gov/divisions/social-services).\n"
        "- 911 (if this is a life-threatening emergency, call 911).\n"
        "- 988's live counselors (for suicidal thoughts or a mental-"
        "health crisis, call or text 988).\n"
        "- The EBT card-services line (for card issues, call the number "
        "on the back of your EBT card; for benefit-eligibility "
        "questions, contact your local DSS office, not this line).\n\n"
        "If you'd like, we can continue with the FNS eligibility "
        "questions."
    )


__all__ = [
    "detect_out_of_scope",
    "scope_redirect_message",
    "detect_not_a_person_request",
    "not_a_person_response",
]
