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

from ..branding import PRODUCT_NAME

# Off-topic patterns. Each entry is a literal phrase (case-insensitive
# substring match) that, when present in the user's message, signals the
# request is outside the NC FNS initial screen scope.
_OUT_OF_SCOPE_PHRASES: tuple[str, ...] = (
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
    "ayuda legal",
    # Immigration
    "inmigracion",
    "caso de inmigracion",
    # Tax / IRS
    "declaracion de impuestos",
    "impuestos",
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

# Profession words are ordinary intake answers ("I work at a lawyer's
# office"). Require an advice-seeking frame instead of refusing bare mentions.
_LEGAL_ADVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # A standalone profession word is itself an off-topic legal request, while
    # a profession embedded in an income answer is merely occupational context.
    re.compile(r"^\s*(?:lawyer|attorney)\s*[.!?]?\s*$", re.IGNORECASE),
    re.compile(r"\b(?:can|could|should|may)\s+i\s+sue\b", re.IGNORECASE),
    re.compile(r"\b(?:can|could|would)\s+you\s+help\s+me\s+sue\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:want|need|plan|intend|had)\s+(?:to\s+)?sue\b", re.IGNORECASE),
    re.compile(r"\b(?:can|could|may)\s+i\s+(?:get|obtain)\s+legal\s+advice\b", re.IGNORECASE),
    re.compile(r"\bhow\s+do\s+i\s+take\b.{0,30}\bsmall\s+claims\s+court\b", re.IGNORECASE),
    re.compile(r"\b(?:do|will|would)\s+i\s+need\s+(?:an?\s+)?(?:lawyer|attorney)\b", re.IGNORECASE),
    re.compile(r"\b(?:can|could|should|may)\s+i\s+(?:talk|speak)\s+to\s+(?:an?\s+)?(?:lawyer|attorney)\b", re.IGNORECASE),
    re.compile(r"\b(?:give|need|want|seeking|provide)\b.{0,30}\b(?:legal\s+advice|lawyer|attorney)\b", re.IGNORECASE),
    re.compile(r"\b(?:file|start|bring)\b.{0,20}\b(?:a\s+)?(?:lawsuit|small\s+claims\s+(?:court|case))\b", re.IGNORECASE),
)


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

    if any(pattern.search(normalized) for pattern in _LEGAL_ADVICE_PATTERNS):
        return True
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
            f"Agradezco que lo compartas, pero solo soy {PRODUCT_NAME} y "
            "solo puedo ayudar con una evaluación inicial de elegibilidad "
            "para los Servicios de Alimentos y Nutrición de Carolina del "
            "Norte (FNS / SNAP). No puedo dar asesoría legal ni ayudar con "
            "temas no relacionados.\n\n"
            "Si quieres, podemos continuar con las preguntas de elegibilidad "
            "de FNS — solo necesito el tamaño de tu hogar, tus ingresos "
            "mensuales brutos y si alguna persona del hogar tiene 60 años "
            "o más o tiene una discapacidad."
        )
    return (
        f"I appreciate you sharing that, but I'm {PRODUCT_NAME} and I'm "
        "only able to help with an initial screen for North Carolina Food "
        "and Nutrition Services (FNS / SNAP) eligibility. I can't give "
        "legal advice, and I can't help with unrelated topics.\n\n"
        "If you'd like, we can keep going with the FNS eligibility "
        "questions — I just need your household size, your gross "
        "monthly income, and whether anyone in the household is 60+ or "
        "disabled."
    )


# W21 — "what this is NOT" disambiguation layer.
#
# Researched failure mode (2026-07-23): a user can mistakenly believe
# FoodPath IS a live person, IS 911 / 988's live counselors, IS an EBT
# card-services line, or IS going to dispatch a DSS caseworker on their
# behalf. FoodPath is none of those. Real hotlines identify themselves at
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
# user who is asking about FoodPath's own nature, so it is third in the
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
    "are you a robot",
    "is this a robot",
    "you are a robot",
    "you're a robot",
    "you are a bot",
    "you're a bot",
    "you are an ai",
    "you're an ai",
    "am i talking to a person",
    "am i talking to a human",
    "am i talking to a bot",
    "is a human reading this",
    "is a person reading this",
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
    "is this a real caseworker",
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
    # P0.2 — identity-class subset. These are also listed in
    # _IDENTITY_QUESTIONS_EN below for the strict-subset routing in
    # detect_identity_question(). Keep the two lists in sync: if a
    # phrase is added here, add it there too (and to the ES list).
    "who are you",
    "what are you",
    "what do you do",
    "what do u do",
    "what is this",
    "what is foodpath",
    "what's foodpath",
    "how does this work",
    "how does foodpath work",
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
    # P0.2 — identity-class subset (Spanish). Also listed in
    # _IDENTITY_QUESTIONS_ES below for the strict-subset routing.
    "quien eres",
    "que eres",
    "que haces",
    "como funciona esto",
    "que es foodpath",
)
# P0.2 — identity-class phrases. These are the "who are you / what do you
# do / what is this" family. The strict identity subset is an ADDITIONAL
# routing choice layered on top of the broader keyword detector: the
# same phrases also appear in _NOT_A_PERSON_PHRASES below so that
# ``detect_not_a_person_request`` catches them too. If the two lists
# drift apart, a reordering of the agent's guardrail chain could let
# identity questions fall through to the classifier and re-introduce
# the "I appreciate you sharing that" non-sequitur.
_IDENTITY_QUESTIONS_EN: tuple[str, ...] = (
    "who are you",
    "what are you",
    "what do you do",
    "what do u do",
    "what is this",
    "what is foodpath",
    "what's foodpath",
    "how does this work",
    "how does foodpath work",
)
_IDENTITY_QUESTIONS_ES: tuple[str, ...] = (
    "quien eres",
    "qué eres",
    "que eres",
    "qué haces",
    "que haces",
    "cómo funciona esto",
    "como funciona esto",
    "qué es foodpath",
    "que es foodpath",
)
_IDENTITY_PATTERNS_EN = _compile_bounded_phrases(_IDENTITY_QUESTIONS_EN)
_IDENTITY_PATTERNS_ES = _compile_bounded_phrases(_IDENTITY_QUESTIONS_ES)

_NOT_A_PERSON_PATTERNS = _compile_bounded_phrases(_NOT_A_PERSON_PHRASES)
_NOT_A_PERSON_PATTERNS_ES = _compile_bounded_phrases(_NOT_A_PERSON_PHRASES_ES)


def detect_identity_question(text: str) -> bool:
    """Return ``True`` iff *text* matches the identity-class subset.

    Stricter than :func:`detect_not_a_person_request` — only the
    "who are you / what do you do / what is this" family. These are
    the cases that deserve a short identity answer + resume with the
    next missing field, not the full 4-channel "what this is NOT" block.
    """
    if not text or not text.strip():
        return False
    normalized = text.casefold().replace("’", "'").replace("‘", "'")
    accentless = normalized.translate(_SPANISH_ACCENTS)
    for pattern in _IDENTITY_PATTERNS_EN:
        if pattern.search(normalized):
            return True
    for pattern in _IDENTITY_PATTERNS_ES:
        if pattern.search(normalized) or pattern.search(accentless):
            return True
    return False


def short_identity_response(
    lang: str = "en", missing: list[str] | None = None
) -> str:
    """Return a short identity answer + the next missing field's question.

    Used for the strict identity-class questions ("who are you", "what
    do you do") where the full 4-channel "what this is NOT" block is
    over-fitted, and the user still needs to be brought back to the
    intake with the next missing field appended.

    The identity sentence is short and references the actual product
    name so the user has a concrete answer to "who are you?" — it does
    not enumerate hotlines, never re-asks filled fields, and never
    opens with "I appreciate you sharing that" or any canned
    acquisition-style opener that would be a non-sequitur to a simple
    identity question.
    """
    missing = list(missing or [])
    if lang == "es":
        opener = (
            f"Soy {PRODUCT_NAME}, un asistente automatizado para una "
            "evaluación inicial de elegibilidad para FNS / SNAP de "
            "Carolina del Norte. No soy una persona real y no puedo "
            "tomar acciones en el mundo real — solo puedo hacer esa "
            "evaluación inicial."
        )
    else:
        opener = (
            f"I'm {PRODUCT_NAME}, an automated guide for an initial "
            "NC FNS / SNAP eligibility estimate. I'm not a real person "
            "and I can't take any real-world action — I just do this "
            "one initial screen."
        )
    if not missing:
        return opener
    # The intake order is fixed by state.missing_fields; use the
    # canonical next-field question map so the prompt stays in
    # lockstep with the rest of the agent.
    from ..agent_questions import NEXT_FIELD_QUESTION
    question = NEXT_FIELD_QUESTION.get(lang, NEXT_FIELD_QUESTION.get("en", {})).get(
        missing[0], ""
    )
    if question:
        return f"{opener} {question}"
    return opener


def detect_not_a_person_request(text: str) -> bool:
    """Return ``True`` if *text* is a question about FoodPath's own nature.

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

    Named after the real hotlines / lines that FoodPath can be confused
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
            f"{PRODUCT_NAME} NO es:\n"
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
        f"{PRODUCT_NAME} is NOT:\n"
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
