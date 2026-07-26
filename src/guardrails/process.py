"""Deterministic answers for in-scope questions about the screening process.

These questions are not off-topic: a person deciding whether to share even
ordinary eligibility information is entitled to know what the screen is,
what it does with the information, and what happens next.  Keeping the
answers here avoids asking a model to improvise trust-critical claims about
privacy, persistence, agency contact, or whether this is an application.

The handler deliberately answers only the process question.  The caller
appends the canonical next-field question while the intake is incomplete, so
an acknowledgement or process question can never strand the user between
fields.
"""

from __future__ import annotations

import re
from typing import Final

from ..branding import PRODUCT_NAME


PROCESS_CATEGORIES: Final[tuple[str, ...]] = (
    "why",
    "time",
    "privacy",
    "security",
    "application",
    "case_effect",
    "required",
    "after",
    "later",
    "agency_sharing",
    "incorrect",
)


_PROCESS_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "agency_sharing",
        (
            re.compile(r"\b(?:are you|will you|do you)\s+(?:going to\s+)?(?:share|send|contact|notify)\b.{0,80}\b(?:county|dss|agency|caseworker)\b", re.I),
            re.compile(r"\b(?:share|send)\b.{0,60}\b(?:this|my information|my data)\b.{0,40}\b(?:county|dss|agency)\b", re.I),
        ),
    ),
    (
        "privacy",
        (
            re.compile(r"\b(?:do|will|are)\s+you\s+(?:save|store|keep|retain|remember)\b", re.I),
            re.compile(r"\b(?:save|store|keep|retain|remember)\b.{0,40}\b(?:this|that|any of this|my information|my data)\b", re.I),
            re.compile(r"\bwho\s+(?:can|will|gets to)\s+see\b", re.I),
            re.compile(r"\bwho\s+sees\s+(?:this|my information|my data)\b", re.I),
        ),
    ),
    (
        "security",
        (
            re.compile(r"\b(?:is|are)\s+(?:this|my information|my data)\s+(?:secure|private|confidential)\b", re.I),
            re.compile(r"\bhow\s+secure\s+is\s+(?:this|it)\b", re.I),
            re.compile(r"\b(?:is this|how is)\s+(?:this|it)\s+(?:protected|encrypted)\b", re.I),
        ),
    ),
    (
        "application",
        (
            re.compile(r"\b(?:is this|this is)\b.{0,50}\b(?:the actual|an official|a real|the real)?\s*application\b", re.I),
            re.compile(r"\b(?:am i|do i)\s+(?:actually\s+)?(?:applying|filling out an application)\b", re.I),
        ),
    ),
    (
        "case_effect",
        (
            re.compile(r"\b(?:will|does|could)\s+this\s+(?:affect|change)\b.{0,40}\b(?:case|benefits?|application|eligibility)\b", re.I),
            re.compile(r"\b(?:affect|change)\s+my\s+(?:case|benefits?|application|eligibility)\b", re.I),
        ),
    ),
    (
        "required",
        (
            re.compile(r"\b(?:do|does|did)\s+i\s+(?:have|need)\s+to\s+answer\b", re.I),
            re.compile(r"\b(?:must|have)\s+i\s+answer\b", re.I),
            re.compile(r"\bcan\s+i\s+(?:skip|decline|not answer)\b", re.I),
        ),
    ),
    (
        "time",
        (
            re.compile(r"\bhow\s+long\s+(?:does|will|should)\s+(?:this|the screening|the screen)\s+take\b", re.I),
            re.compile(r"\bhow\s+much\s+time\b.{0,30}\b(?:this|screen|screening)\b", re.I),
        ),
    ),
    (
        "why",
        (
            re.compile(r"\bwhy\s+(?:do|are)\s+you\s+(?:need|asking)\b", re.I),
            re.compile(r"\bwhat\s+do\s+you\s+need\s+(?:that|this)\s+for\b", re.I),
        ),
    ),
    (
        "after",
        (
            re.compile(r"\bwhat\s+happens\s+after\s+(?:this|the screen|i finish)\b", re.I),
            re.compile(r"\bwhat\s+do\s+i\s+do\s+after\s+(?:this|the screen|i finish)\b", re.I),
        ),
    ),
    (
        "later",
        (
            re.compile(r"\bcan\s+i\s+(?:do|finish|complete)\s+this\s+later\b", re.I),
            re.compile(r"\bcan\s+i\s+come\s+back\s+later\b", re.I),
        ),
    ),
    (
        "incorrect",
        (
            re.compile(r"\bwhat\s+if\s+i\s+(?:get|got)\s+(?:that|it|this)\s+wrong\b", re.I),
            re.compile(r"\bwhat\s+if\s+(?:my|the)\s+(?:answer|answers|information)\s+is\s+wrong\b", re.I),
        ),
    ),
)


def detect_process_question(text: str) -> str | None:
    """Return the deterministic process-question category, if any."""
    if not isinstance(text, str) or not text.strip():
        return None
    for category, patterns in _PROCESS_PATTERNS:
        if any(pattern.search(text) for pattern in patterns):
            return category
    return None


def process_question_response(category: str, lang: str = "en") -> str:
    """Return a source-grounded answer to one in-scope process question.

    Privacy wording is intentionally precise: FoodPath does not create a
    persistent application/case record or submit to an agency, while the
    implementation may retain redacted operational telemetry. That is more
    honest than claiming that no bytes can ever be retained.
    """
    if lang == "es":
        answers = {
            "why": "Necesito solo tres datos para esta evaluación inicial: el tamaño de tu hogar, los ingresos mensuales brutos y si alguna persona tiene 60 años o más o una discapacidad. No necesito un número de Seguro Social, una dirección ni un teléfono.",
            "time": "Son solo tres preguntas requeridas. El tiempo depende de tus respuestas; esta evaluación no es una solicitud oficial.",
            "privacy": "No guardo tus respuestas como una solicitud o expediente permanente, y FoodPath no envía esta evaluación a una agencia. La sesión se mantiene mientras haces esta evaluación; pueden existir registros técnicos redactados para operar y depurar el servicio. No compartas números de Seguro Social, direcciones ni teléfonos.",
            "agency_sharing": "No. FoodPath no envía esta evaluación ni contacta, avisa o comparte sus datos con el DSS del condado u otra agencia. Es solo una evaluación inicial; para una decisión oficial, presenta la solicitud mediante ePASS o tu oficina del DSS.",
            "security": "No puedo prometer seguridad absoluta. Para proteger tu privacidad, comparte solo los datos generales que pide esta evaluación; nunca pide números de Seguro Social, direcciones ni teléfonos. Esto no es una solicitud ni está conectado a una agencia.",
            "application": "No. Esto es solo una evaluación inicial de elegibilidad, no la solicitud oficial. FoodPath no presenta solicitudes ni contacta a una agencia. Para una decisión oficial, presenta la solicitud mediante ePASS o tu oficina local del DSS.",
            "case_effect": "No debería cambiar ni presentar tu caso: esta evaluación no es una decisión oficial y FoodPath no contacta al DSS. Solo una solicitud y revisión de la agencia determinan la elegibilidad.",
            "required": "Solo necesito los tres campos requeridos para ejecutar la evaluación inicial. No tienes que compartir identificadores personales, y las deducciones opcionales solo se registran si las ofreces voluntariamente.",
            "after": "Cuando terminemos, verás un resultado de evaluación inicial. Si quieres una decisión oficial, presenta la solicitud mediante ePASS o comunícate con la oficina del DSS de tu condado.",
            "later": "Puedes volver más tarde, pero esto no es una solicitud guardada ni un expediente permanente; es posible que tengas que comenzar una nueva evaluación.",
            "incorrect": "Puedes corregir una respuesta diciéndome el valor correcto. El resultado es solo una evaluación inicial; la agencia verificará la información en una solicitud oficial.",
        }
    else:
        answers = {
            "why": "I only need three pieces of information for this initial screen: household size, gross monthly income, and whether anyone is 60 or older or disabled. I do not need a Social Security number, address, or phone number.",
            "time": "There are only three required questions. The time depends on your answers; this screen is not an official application.",
            "privacy": "I do not save your answers as a persistent application or case record, and FoodPath does not submit this screen to an agency. The session is held while you use this screening; redacted technical records may exist to operate and debug the service. Please do not share Social Security numbers, addresses, or phone numbers.",
            "agency_sharing": "No. FoodPath does not submit this screening or contact, notify, or share it with the county DSS or another agency. It is an initial screen only; for an official decision, apply through ePASS or your county DSS office.",
            "security": "I cannot promise absolute security. To protect your privacy, share only the general information this screen asks for; it never asks for Social Security numbers, addresses, or phone numbers. This is not an application and is not connected to an agency.",
            "application": "No. This is only an initial eligibility screen, not the official application. FoodPath does not submit applications or contact an agency. For an official decision, apply through ePASS or your local county DSS office.",
            "case_effect": "It should not change or submit your case: this screen is not an official decision and FoodPath does not contact DSS. Only an agency application and review determine eligibility.",
            "required": "I only need the three required fields to run the initial screen. You do not have to share personal identifiers, and optional deductions are recorded only if you volunteer them.",
            "after": "After we finish, you will see an initial-screen result. For an official decision, apply through ePASS or contact your county DSS office.",
            "later": "You can come back later, but this is not a saved application or permanent case record; you may need to start a new screening.",
            "incorrect": "You can correct an answer by telling me the right value. The result is only an initial screen; the agency verifies information in an official application.",
        }
    return answers.get(category, answers["why"])


__all__ = [
    "PROCESS_CATEGORIES",
    "detect_process_question",
    "process_question_response",
]
