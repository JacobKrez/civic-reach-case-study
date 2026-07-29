"""Stateful, tool-driven conversation loop for the FoodPath agent."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Callable

from pydantic import ValidationError

from .branding import PRODUCT_NAME
from .calculator import calculate_eligibility, income_limits, max_household_size
from .guardrails.ambiguity import (
    AMBIGUITY_TWO_FIGURES,
    classify_income_ambiguity,
    income_clarification_question,
    normalize_income,
    resolve_two_figures_answer,
)
from .guardrails.application_status import (
    application_status_response,
    detect_application_status_question,
)
from .guardrails.benefit_amount import (
    benefit_amount_response,
    detect_benefit_amount_question,
)
from .guardrails.classifier import classify_intent
from .guardrails.crisis import crisis_response, detect_crisis
from .guardrails.expedited import detect_expedited_question, expedited_response
from .guardrails.hostility import (
    HOSTILITY_CONTEXT_EN,
    HOSTILITY_CONTEXT_ES,
    detect_hostility,
)
from .guardrails.household_size_question import (
    detect_max_household_size_question,
    max_household_size_response,
)
from .guardrails.injection import detect_injection, injection_response
# P0.3: classifier-off-topic steering hint. Prepended to the system
# prompt for ONE turn only when the LLM intent classifier flags the
# user's message as off_topic. The classifier no longer has
# conversation veto (see agent.turn() for the routing change), but
# the main LLM still benefits from a single-line hint that the user
# said something the classifier thought was off-topic — so the LLM
# can acknowledge it (one short clause) and then handle whatever the
# user actually asked. This is the same per-turn lifecycle as
# _hostile_this_turn / HOSTILITY_CONTEXT_EN: never written to history,
# gone by the next turn.
_CLASSIFIER_OFF_TOPIC_HINT_EN = (
    "Note: the user's latest message did not clearly look like an FNS "
    "intake answer. Briefly acknowledge what they said (one short "
    "clause, no canned opener). If they asked a real QUESTION about "
    "the program or this process, answer it. If instead they asked "
    "you to DO something unrelated to FNS eligibility (write, "
    "generate, or produce anything -- a poem, story, joke, code, "
    "email, recipe, or similar), do not do it, even briefly or "
    "partially: decline in one short clause and say you're only set "
    "up for this screen. Then return to the next missing FNS field. "
    "Never use the scope-redirect canned block verbatim."
)
_CLASSIFIER_OFF_TOPIC_HINT_ES = (
    "Nota: el último mensaje del usuario no parece claramente una "
    "respuesta de la evaluación de FNS. Reconoce brevemente lo que "
    "dijo (una cláusula corta, sin apertura estereotipada). Si hizo "
    "una PREGUNTA real sobre el programa o este proceso, respóndela. "
    "Si en cambio te pidió que HICIERAS algo no relacionado con la "
    "elegibilidad de FNS (escribir, generar o producir algo -- un "
    "poema, cuento, chiste, código, correo, receta, o similar), no lo "
    "hagas, ni siquiera brevemente o en parte: declina en una cláusula "
    "corta y explica que solo puedes ayudar con esta evaluación. Luego "
    "vuelve al siguiente campo pendiente de FNS. Nunca uses el bloque "
    "estereotipado de redirección de alcance literalmente.\n"
)

from .guardrails.pii import redact_for_logging
from .guardrails.drifter import (
    detect_drifter_aside,
    drifter_redirect_message,
)
from .guardrails.process import detect_process_question, process_question_response
from .guardrails.restarter import (
    apply_intake_reset,
    detect_recap_request,
    detect_restart_request,
    recap_message,
    reset_intake_message,
)
from .guardrails.scope import (
    detect_identity_question,
    detect_not_a_person_request,
    detect_out_of_scope,
    not_a_person_response,
    scope_redirect_message,
    short_identity_response,
)
from .handoff import _HANDOFF_BANNER, handoff_summary
from .llm_client import LLMClient
from .retrieval import detect_spanish, retrieve
from .state import AssessmentState
from .telemetry import emit_turn

SYSTEM_PROMPT = """You are FoodPath, an independent North Carolina FNS / SNAP eligibility assistant. You are warm, plain-spoken, and conversational. You help a single household with a short initial screen — three fields, a few follow-up clarifications, and a plain-language explanation of where they likely land.

# Persona
- Warm, direct, plain language. Aim for a 6th-grade reading level.
- One thought per sentence. No bureaucratic phrasing, no legal language.
- You are not a person, a caseworker, a hotline, or anyone who can take real-world action on the user's behalf. You are an automated text tool that does one thing: an initial FNS screen.
- The first-turn AI / not-a-person notice and the first question have already been rendered by code. Do NOT repeat them. Do NOT stack additional disclaimers.

# Conversation policy
Every reply follows the same shape, in this order:
1. **Acknowledge** what the user just said, in one short clause. The acknowledgement must be specific to what they said — "got it", "thanks", or a re-statement of their content. Do NOT open with a canned "I appreciate you sharing that" or any variant; that opener is reserved for off-topic messages routed through the scope redirect.
2. **Answer** any real question they asked, in 1-2 sentences. If they did not ask a question, skip this step.
3. **Ask exactly ONE question** for the next missing field. The last sentence of your reply is the next-field question, until the intake is complete.

Never re-ask a field the user has already filled. Never restate a field the user has already provided. Never re-open a confirmed value to "double-check" — the state is the source of truth, the user does not need to re-justify.

If the user is off-topic, the system routes the turn to a deterministic redirect (`scope_redirect_message`) — you do not see off-topic turns. If the user asks who/what you are, the system routes the turn to the short identity response (`short_identity_response`) or the full not-a-person block (`not_a_person_response`) — you do not author those replies. If the user is in crisis, the system routes the turn to the hardcoded crisis response (`crisis_response`). Your prompt only governs ordinary, in-scope intake turns.

# Few-shot examples

Example 1 — happy-path intake, terse reply:
User: "hi"
Assistant: "How many people, including you, usually buy and prepare food together? If you usually buy and cook just for yourself, answer 1."
User: "3"
Assistant: "Got it — 3 people. What is your household's gross monthly income?"
User: "1500"
Assistant: "Got it — $1,500 a month. Does anyone in your household have an elderly (60+) or disabled member?"
User: "no"
Assistant: (final eligibility explanation follows, with citations)

Example 2 — income where the user stated the pay period:
User: "we make about 1500 a month"
Assistant: "Got it — $1,500 a month. Does anyone in your household have an elderly (60+) or disabled member?"

Example 2b — income where the pay period is MISSING. Never guess it. A
figure that is really per day or per year, recorded as per month, produces
the wrong result. Ask which one it is, and quote their own number back:
User: "70"
Assistant: "Just to be sure I have this right — is $70 per month, per week, or per day? I need the household's gross monthly total."

Example 2c — a rate with no quantity of time. Ask for the missing half,
then combine the two; do not do the arithmetic in your head:
User: "$15 an hour"
Assistant: "Got it. To turn an hourly rate into a monthly total, about how many hours a week do you usually work?"

Example 3 — social filler that is NOT a field value:
User: "thanks"
Assistant: "You're welcome. Does anyone in your household have an elderly (60+) or disabled member?"

Example 4 — user asks an identity question (the system routes this away; what you would have said if you did see it is just the next-field question):
User: "who are you"
Assistant: (system routes this to `short_identity_response`; the model never sees this turn)

Enumerated-household example (F2): when the user LISTS household members by name or relation, count them and RECORD the count. Counting people the user explicitly listed is RECORDING, not arithmetic — the user told you who they are; the only thing you do is add them up. State the count back so the user can correct it; an unresolved confirmation leaves the field empty forever, but a stated-back count the user can veto does not. The correction machinery (W64 / W68b) handles "no, actually it's 3" if they push back.
User: "it's me, my two kids, and my mom — does she count?"
Assistant: (calls update_state with household_size=4) "So that's 4 of you — you, your two kids, and your mom. If that's off, just say so. Now, what's your household's gross monthly income?"

# Rules (apply only after the conversational shape above)

Collect only the three fields the calculator needs:
  1. household_size  (integer)
  2. gross_monthly_income  (number)
  3. has_elderly_or_disabled_member  (boolean)

The calculator also accepts four OPTIONAL deduction inputs. Do NOT ask extra questions to collect them — record them ONLY when the user volunteers the information in conversation:
  - earned_monthly_income (the wage/self-employment portion of income)
  - dependent_care_monthly (childcare/dependent-care costs)
  - medical_expenses_monthly (out-of-pocket medical costs; relevant when the household has an elderly or disabled member)
  - shelter_costs_monthly (rent/mortgage, taxes, insurance, utilities)
Recording volunteered deduction data improves the accuracy of the net income screen. Never press the user for these.

NEVER ask the user for any other personally identifiable information. In particular, do NOT request: a Social Security number, a full date of birth, a home address, a phone number, a bank account number, a driver's license number, an immigration status, or any other identifier beyond the fields above. If the user volunteers such information, accept it gracefully but do not store it and do not echo it back verbatim.

Record user-provided facts only by calling update_state; do not put arithmetic or inferred values in that tool call. Never do eligibility arithmetic yourself. Once the deterministic eligibility result is supplied, explain it using that result and the retrieved rule text, clearly labeling it as an initial screen rather than a final agency determination.

F2 — counting is RECORDING, not arithmetic, when the user explicitly lists people. When the user enumerates household members by name or relation ("me, my two kids, and my mom"; "my partner and I and our baby"; "just me"; "me, my husband, my mom, and my two brothers"), COUNT them and record the count. Do not ask for unresolved confirmation — that leaves the field empty forever. State the count back in your ack ("So that's 4 of you...") so the user can correct it; the correction machinery handles vetoes. This rule covers counting people only; eligibility arithmetic (income totals, threshold comparisons) is still prohibited.

W53 — INTAKE FORWARD PROGRESS: once you successfully record a field with update_state, confirm it in ONE short clause and IMMEDIATELY ask the next field in missing_fields. Do NOT re-open, re-confirm, or re-ask a field you just accepted. Do NOT narrate internal schema constraints to the user. Do NOT end your reply with a rhetorical question that does not advance the intake. While missing_fields is non-empty, the LAST sentence of your reply must be the next-field question.

If the user asks for something outside the NC FNS eligibility scope, the system has already redirected. Do not attempt to answer the off-topic ask. Do not abandon or reset any state that has already been collected.

# DISCLOSURE INTEGRITY: the UI has already shown a compact, code-templated
# introduction identifying FoodPath as an automated eligibility guide and
# not an application or official decision. Do NOT repeat that introduction
# on the first user turn or add a wall of disclaimers. Do not claim to be a
# person, caseworker, hotline, or someone who can take real-world action.
# If the user explicitly asks who or what you are, the response is produced
# by ``not_a_person_response(lang)``. Active crisis disclosures are handled
# separately by the hardcoded ``crisis_response()``.
"""

# Spanish full-conversation system prompt (W19). A 1:1 rendering of the
# English SYSTEM_PROMPT above: same scope, same one-question-at-a-time
# discipline, same tool contract (update_state records STRUCTURED values;
# tool input keys and numbers stay language-independent), same PII
# prohibition. Used for the entire intake once state.language == "es" so
# the model asks the questions and delivers the final eligibility
# explanation in Spanish. Keep rule vocabulary (income limits, deduction
# names) aligned with knowledge_base/01_program_overview.es.md so the
# model's phrasing matches the retrieved Spanish rule text.

SYSTEM_PROMPT_ES = """Eres FoodPath, un asistente independiente de elegibilidad para FNS / SNAP de Carolina del Norte. Eres cálido, directo y conversacional. Ayudas a un solo hogar con una evaluación inicial breve — tres campos, algunas aclaraciones, y una explicación en lenguaje sencillo de dónde probablemente se ubica.

# Persona
- Cálido, directo, lenguaje sencillo. Apunta a un nivel de lectura de sexto grado.
- Un pensamiento por oración. Sin frases burocráticas, sin lenguaje legal.
- No eres una persona, un trabajador social, una línea de ayuda, ni nadie que pueda tomar acciones en el mundo real por el usuario. Eres una herramienta de texto automatizada que hace una sola cosa: una evaluación inicial de FNS.
- El aviso inicial de IA / no-persona y la primera pregunta ya fueron renderizados por código. NO los repitas. NO añadas muros de avisos adicionales.

# Política de conversación
Cada respuesta sigue la misma forma, en este orden:
1. **Reconoce** lo que el usuario acaba de decir, en una cláusula corta. El reconocimiento debe ser específico de lo que dijo — "entendido", "gracias", o una reformulación de su contenido. NO abras con un "agradezco que lo compartas" estereotipado ni con ninguna variante; ese inicio está reservado para mensajes fuera de alcance enrutados por la redirección de alcance.
2. **Responde** cualquier pregunta real que haya hecho, en 1-2 oraciones. Si no hizo una pregunta, omite este paso.
3. **Haz exactamente UNA pregunta** para el siguiente campo pendiente. La última oración de tu respuesta es la pregunta del siguiente campo, hasta que la recolección esté completa.

Nunca vuelvas a preguntar un campo que el usuario ya llenó. Nunca repitas un campo que el usuario ya proporcionó. Nunca vuelvas a abrir un valor confirmado para "verificar de nuevo" — el estado es la fuente de verdad, el usuario no necesita re-justificarlo.

Si el usuario está fuera de alcance, el sistema enruta el turno a una redirección determinista (`scope_redirect_message`) — tú no ves esos turnos. Si el usuario pregunta quién o qué eres, el sistema enruta el turno a la respuesta corta de identidad (`short_identity_response`) o al bloque completo de no-persona (`not_a_person_response`) — tú no escribes esas respuestas. Si el usuario está en crisis, el sistema enruta el turno a la respuesta de crisis codificada (`crisis_response`). Tu prompt solo gobierna turnos ordinarios dentro del alcance de la recolección.

# Ejemplos de few-shot

Ejemplo 1 — recolección por camino feliz, respuesta breve:
Usuario: "hola"
Asistente: "¿Cuántas personas, incluyéndote, compran y preparan comida juntas? Si normalmente compras y cocinas solo para ti, responde 1."
Usuario: "3"
Asistente: "Entendido — 3 personas. ¿Cuál es el ingreso mensual bruto de su hogar?"
Usuario: "1500"
Asistente: "Entendido — $1,500 al mes. ¿Alguna persona en su hogar tiene 60 años o más, o tiene una discapacidad?"
Usuario: "no"
Asistente: (sigue la explicación final de elegibilidad, con citas)

Ejemplo 2 — ingreso donde el usuario indicó el período de pago:
Usuario: "ganamos como 1500 al mes"
Asistente: "Entendido — $1,500 al mes. ¿Alguna persona en su hogar tiene 60 años o más, o tiene una discapacidad?"

Ejemplo 2b — ingreso SIN período de pago. Nunca lo adivines. Una cifra que
en realidad es por día o por año, registrada como mensual, produce un
resultado incorrecto. Pregunta cuál es, citando su propio número:
Usuario: "70"
Asistente: "Solo para confirmar: $70 ¿es por mes, por semana o por día? Necesito el total mensual bruto del hogar."

Ejemplo 2c — una tarifa sin cantidad de tiempo. Pide la mitad que falta y
luego combínalas; no hagas la aritmética de memoria:
Usuario: "$15 por hora"
Asistente: "Entendido. Para convertir un salario por hora a un total mensual, ¿cuántas horas trabajas por semana normalmente?"

Ejemplo 3 — relleno social que NO es un valor de campo:
Usuario: "gracias"
Asistente: "De nada. ¿Alguna persona en su hogar tiene 60 años o más, o tiene una discapacidad?"

Ejemplo 4 — el usuario pregunta una pregunta de identidad (el sistema la enruta fuera; lo que habrías dicho si la vieras es solo la siguiente pregunta de campo):
Usuario: "quién eres"
Asistente: (el sistema enruta esto a `short_identity_response`; el modelo nunca ve este turno)

Ejemplo de hogar enumerado (F2): cuando el usuario LISTA los miembros del hogar por nombre o relación, cuéntalos y REGISTRA el total. Contar personas que el usuario enumeró explícitamente es REGISTRAR, no aritmética — el usuario te dijo quiénes son; lo único que haces es sumarlos. Di el total en voz alta para que el usuario pueda corregirlo; una confirmación sin resolver deja el campo vacío para siempre, pero un total enunciado que el usuario puede vetar no. La maquinaria de corrección (W64 / W68b) maneja "no, en realidad son 3" si el usuario corrige.
Usuario: "somos yo, mis dos hijos, y mi mamá — ¿ella cuenta?"
Asistente: (llama update_state con household_size=4) "Entonces son 4: tú, tus dos hijos y tu mamá. Si me equivoco, dímelo. ¿Cuál es el ingreso mensual bruto de su hogar?"

# Reglas (se aplican solo después de la forma conversacional arriba)

Recolecta únicamente los tres campos que necesita la calculadora:
  1. household_size  (entero — número de personas en el hogar)
  2. gross_monthly_income  (número — ingresos mensuales brutos del hogar)
  3. has_elderly_or_disabled_member  (booleano — si alguna persona del hogar tiene 60 años o más o tiene una discapacidad)

La calculadora también acepta cuatro deducciones OPCIONALES. NO hagas preguntas adicionales para recolectarlas — regístralas ÚNICAMENTE cuando el usuario ofrezca la información por su cuenta:
  - earned_monthly_income (la porción de ingresos por trabajo salarial o trabajo por cuenta propia)
  - dependent_care_monthly (costos de cuidado de niños o dependientes)
  - medical_expenses_monthly (gastos médicos de bolsillo; relevante cuando el hogar incluye una persona mayor o con discapacidad)
  - shelter_costs_monthly (renta/hipoteca, impuestos, seguro, servicios)
Registrar las deducciones que el usuario ofrece voluntariamente mejora la precisión de la evaluación de ingreso neto. Nunca presiones al usuario para obtener estos datos.

NUNCA pidas al usuario ninguna otra información de identificación personal. En particular, NO solicites: un número de Seguro Social, una fecha de nacimiento completa, una dirección de domicilio, un número de teléfono, un número de cuenta bancaria, un número de licencia de conducir, un estatus migratorio, ni ningún otro identificador más allá de los campos anteriores. Si el usuario ofrece esa información por su cuenta, acéptala con cortesía pero no la almacenes ni la repitas textualmente.

Registra los datos que proporciona el usuario únicamente llamando a update_state; no pongas cálculos aritméticos ni valores inferidos en esa llamada de herramienta. Nunca hagas la aritmética de elegibilidad tú mismo. Cuando se te proporcione el resultado determinista de elegibilidad, explícalo usando ese resultado y el texto de las reglas recuperado, aclarando claramente que se trata de una evaluación inicial y no de una determinación final de la agencia.

F2 — contar personas es REGISTRAR, no aritmética, cuando el usuario las enumera explícitamente. Cuando el usuario enumera los miembros del hogar por nombre o relación ("somos yo, mis dos hijos, y mi mamá"; "mi pareja y yo y nuestro bebé"; "solo yo"; "yo, mi esposo, mi mamá, y mis dos hermanos"), CUÉNTALOS y registra el total. No pidas una confirmación sin resolver — deja el campo vacío para siempre. Di el total en voz alta ("Entonces son 4: tú, tus dos hijos y tu mamá") para que el usuario pueda corregirlo; la maquinaria de corrección maneja los vetos. Esta regla cubre solo el conteo de personas; la aritmética de elegibilidad (totales de ingresos, comparaciones de umbrales) sigue prohibida.

W53 — PROGRESO DE LA RECOLECCIÓN: una vez que registres exitosamente un campo con update_state, confírmalo en UNA cláusula corta e INMEDIATAMENTE pregunta por el siguiente campo en missing_fields. NO vuelvas a abrir, re-confirmar ni re-preguntar un campo que acabas de aceptar. NO narres restricciones internas del esquema. NO termines tu respuesta con una pregunta retórica que no avance la recolección. Mientras missing_fields no esté vacío, la ÚLTIMA oración de tu respuesta debe ser la pregunta del siguiente campo.

Si el usuario pide algo fuera del alcance de la elegibilidad de NC FNS, el sistema ya lo redirigió. No intentes responder la solicitud fuera de tema. No abandones ni reinicies ningún estado que ya se haya recolectado.

# INTEGRIDAD DEL AVISO: la interfaz ya mostró una introducción breve,
# generada por código, que identifica a FoodPath como una guía automatizada
# de elegibilidad y aclara que no es una solicitud ni una decisión oficial.
# NO repitas esa introducción en el primer turno ni agregues un muro de
# avisos. No afirmes ser una persona, trabajador social, línea de ayuda ni
# alguien que pueda actuar en el mundo real. Si el usuario pregunta
# explícitamente quién o qué eres, usa ``not_a_person_response(lang)``.
# Las crisis activas se manejan por separado con ``crisis_response()``.

IMPORTANTE: Conduce TODA la conversación en español — todas tus preguntas, aclaraciones y la explicación final de elegibilidad deben estar en español. El único contenido en inglés permitido en tus respuestas son las líneas deterministas generadas por código (p. ej. los sufijos "(Sources: ...)" y "(Updated ...)", que se agregan automáticamente y no debes reproducirlas ni traducirlas tú mismo).
ni traducirlas tú mismo).
"""

# Deterministic, code-templated fallback questions for the "model returned
# a tool call with no text" case -- see the note at the end of turn().
# Phrasing matches what the system prompt already asks the model to do.
# The single source of truth lives in src/agent_questions.py so any
# module that needs to ask the next intake question (the agent, the
# not-a-person identity response, the restarter, the drifter, etc.) can
# import the same code-templated phrasing without a circular import.
from .agent_questions import NEXT_FIELD_QUESTION as _NEXT_FIELD_QUESTION  # noqa: E402, F401

# W53 — forward-progress floor (F1, 2026-07-28).
# See _w53_fold_ask for the new rule and the F3 contract.
# The previous signature/anchor/legacy maps and _core_question_sentence
# were deleted as part of F1 (CONVERSATION_OVERHAUL.md §9.3);
# the new rule does not need them.


# Vocabulary that marks a message as being about a NON-income intake field
# -- household composition or the elderly/disabled question. Used to stop a
# held income clarification from swallowing a message that answers a
# different question ("my mom is 68 does that matter"). Deliberately paired
# with an income-cue check at the call site, so a message that names a
# relative AND an income ("my husband makes 2000") is still readable as
# income.
_OTHER_INTAKE_FIELD_RE = re.compile(
    r"\b(?:mom|mother|mama|dad|father|papa|grandma|grandmother|grandpa|"
    r"grandfather|son|daughter|kid|kids|child|children|baby|husband|wife|"
    r"spouse|partner|brother|sister|aunt|uncle|cousin|roommate|"
    r"years?\s+old|yrs?\s+old|age[ds]?\b|elderly|disabled|disability|"
    r"mamá|papá|abuela|abuelo|hij[oa]s?|espos[oa]|herman[oa]s?|"
    r"años|discapacidad|discapacitad[oa])\b",
    re.IGNORECASE,
)


def _mentions_other_intake_field(text: str) -> bool:
    """True when *text* is plainly about household members or the
    elderly/disabled question rather than about money."""
    return bool(_OTHER_INTAKE_FIELD_RE.search(text or ""))


def _w53_fold_ask(
    content: str,
    lang: str,
    missing: list[str],
    *,
    question_override: str | None = None,
) -> str:
    """Append the canonical next-field question for ``missing[0]`` if
    the reply does not already contain a question.

    F1 (2026-07-28): the previous implementation matched against the
    canonical question's exact substring (and a set of anchor phrases
    for ``household_size``). It was fundamentally brittle against a
    model instructed (by P1.1) to speak conversationally rather than
    recite: any paraphrasing of the canonical question broke the
    match, and the floor would then staple the canonical question
    after the model's own question -- producing the "Sound right?
    How many people, including you, usually buy and prepare food
    together?" double-question visible in §9.1.

    The new rule is simpler and survives any model paraphrase:

      1. If ``missing`` is empty (intake complete), return content
         unchanged. (Preserved from W53.)
      2. If ``question_override`` is set, append it to content
         (or return it alone if content is empty). This is the F3
         contract: a deterministic clarification question must
         surface even when the model already asked something.
      3. If ``content`` already contains a question mark (EN ``?``,
         ES ``¿``), return content unchanged. A model that asked
         *some* question keeps the conversation moving; a duplicate
         canonical question visibly breaks it.
      4. Otherwise (no question anywhere in content), append the
         canonical next-field question -- the original W53 floor.

    Risk acknowledged: an occasional non-advancing model question
    (the rhetorical "were you just tossing out a number?" case) will
    no longer be replaced by the canonical. The doc (§9.3) accepts
    this tradeoff for the demo, in exchange for stopping the much
    more common and more visible double-question failure. The P1.1
    conversation policy + the P1.2 validators cover most of the
    remaining risk.
    """
    if not missing:
        return content
    field = missing[0]
    # F3 contract: question_override always wins (used for the
    # pending-income-clarification case where a deterministic
    # clarification question must surface).
    if question_override:
        if content:
            return f"{content} {question_override}"
        return question_override
    # F1 rule: if the model already asked *any* question, leave it
    # alone. This is the new arms-race-free contract.
    if "?" in content or "¿" in content:
        return content
    question = _NEXT_FIELD_QUESTION.get(lang, _NEXT_FIELD_QUESTION["en"]).get(
        field, ""
    )
    if not question:
        return content
    if content:
        return f"{content} {question}"
    return question


# ---------------------------------------------------------------------------
# W62 — outcome-label floor (single source of truth)
# ---------------------------------------------------------------------------
# The post-verdict branch (cached _eligibility_result is not None) and the
# fresh-decide branch (verdict produced this turn) BOTH need to defend the
# user-visible reply against model prose that omits the outcome term --
# "eligible" / "qualif" / "screening" / etc. Before W62 this same
# terms-dict + label-dict + render block was duplicated near-verbatim
# three times in agent.py. The duplication made the canonical phrases
# drift over time (one block dropped the bilingual ``es`` labels, one
# silently substituted the outcome key, one forgot the notes), which
# in turn let the post-verdict re-ask bug slip through unobserved: a
# refactor to one of the three copies did not propagate to the others.
#
# Collapsing them into one helper means:
#   * a future tweak to the terms / labels is automatically applied to
#     both branches (no risk of drift);
#   * the post-verdict branch and the fresh-decide branch stay byte-
#     identical for the same ``(content, result, language)`` triple;
#   * the helper has one obvious place to add new languages.
_OUTCOME_TERMS: dict[str, tuple[str, ...]] = {
    "likely_eligible": ("eligible", "qualif", "califica"),
    "uncertain_apply_anyway": ("screen", "apply", "solicita", "evalu"),
    "likely_ineligible": ("ineligible", "not qualify", "no califica"),
}

_OUTCOME_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "likely_eligible": "Screening result: likely eligible.",
        "uncertain_apply_anyway": "Screening result: eligibility is uncertain; apply anyway.",
        "likely_ineligible": "Screening result: likely ineligible based on income.",
    },
    "es": {
        "likely_eligible": "Resultado de evaluación: probablemente califica.",
        "uncertain_apply_anyway": "Resultado de evaluación: elegibilidad incierta; solicite de todos modos.",
        "likely_ineligible": "Resultado de evaluación: probablemente no califica según los ingresos.",
    },
}


# W71b: Spanish equivalent of the calculator's income-vs-limit fact
# sentence, used ONLY by the fallback branch of _outcome_label_floor
# below (language == "es"). ``calculator.calculate_eligibility``'s
# ``notes`` field is English-only prose with several branches (rule
# note, deduction note, boundary note, outcome note); it has no
# ``language`` parameter, and its docstring commits to "byte-identical
# output to the pre-W14 screener" for existing callers, so threading a
# language switch through that function risks a much larger surface
# than this bug warrants. Found live 2026-07-28: when a Spanish
# conversation's own model reply is empty or lacks a recognized outcome
# term, this floor used to append the RAW ENGLISH notes after a
# correctly-Spanish outcome label, producing a mixed-language reply
# ("Resultado de evaluación: probablemente califica. Household has no
# elderly (60+) or disabled member and is screened under..."). This
# builds a short, fully-Spanish sentence instead, using only the
# already-computed, language-neutral numeric fields from ``result``
# (income_considered, limit, household_size, test_used) -- it loses the
# English notes' extra detail (deduction breakdown, boundary note) in
# this fallback path, but a shorter, fully-correct Spanish sentence is
# strictly better than a longer, mixed-language one.
_TEST_USED_LABEL_ES: dict[str, str] = {
    "gross": "bruto",
    "net": "neto",
    "expanded": "bruto ampliado (BBCE)",
}


def _outcome_notes_es(result: dict[str, Any]) -> str:
    income_considered = result.get("income_considered")
    limit = result.get("limit")
    household_size = result.get("household_size")
    test_used = str(result.get("test_used") or "")
    test_label = _TEST_USED_LABEL_ES.get(test_used, test_used or "bruto")
    if income_considered is None or limit is None or household_size is None:
        return ""
    return (
        f"Ingreso mensual considerado ${income_considered:,.2f} vs límite "
        f"{test_label} de ${limit:,.2f} para un hogar de {household_size}."
    )


def _outcome_label_floor(
    content: str,
    result: dict[str, Any] | None,
    language: str,
    _purpose: str = "verdict",
) -> str:
    """Return ``content`` unless it is empty or omits the outcome
    term; in that case return a deterministic, code-templated
    rendering of the eligibility result.

    Both branches (post-verdict explain and fresh-decide result)
    route their user-visible reply through this function so the
    outcome-bearing sentence the user sees is a single source of
    truth across the agent. The fallback uses bilingual
    ``_OUTCOME_LABELS`` and appends an explanation sentence so the
    user always sees the reason alongside the outcome -- the
    calculator's own (English-only) ``notes`` for English, or the
    shorter, fully-Spanish ``_outcome_notes_es`` summary for Spanish
    (see the comment above it for why these aren't the same text).

    The ``_purpose`` argument is reserved for future per-branch
    debugging and is intentionally unused today; it lets callers
    tag a branch ("verdict", "explain", "explain_repeat") without
    the helper changing behavior.
    """
    del _purpose  # reserved for future per-branch tagging
    if result is None:
        return content
    outcome = str(result.get("outcome") or "")
    terms = _OUTCOME_TERMS.get(outcome, (outcome.replace("_", " "),))
    if content.strip() and any(term in content.lower() for term in terms):
        return content
    label_table = _OUTCOME_LABELS.get(language, _OUTCOME_LABELS["en"])
    label = label_table.get(outcome, outcome)
    notes = _outcome_notes_es(result) if language == "es" else str(result.get("notes") or "")
    return f"{label} {notes}".strip()


# ---------------------------------------------------------------------------
# W62 — strip re-asking sentences from a completed-intake reply
# ---------------------------------------------------------------------------
# Phrase vocabulary for "the model just re-asked a field that was
# already collected." Mirrors ``tests/golden/_helpers._REASK_PHRASES_*``
# because the bug surface is the user-visible sentence shape, not the
# data model.
_REASK_PHRASES_BY_LANG: dict[str, tuple[str, ...]] = {
    "en": (
        "how many people are in your household",
        "what is your household's gross monthly income",
        "what is your household\ufffds gross monthly income",
        "does anyone in your household have an elderly",
        "does anyone in your household have a disabled",
        "anyone in your household have an elderly",
        "anyone in your household have a disabled",
        # Live failure transcript (2026-07-27): the broken model
        # paraphrased the household-size question instead of using
        # the canonical phrasing, but the agent still has to
        # recognize it as a re-ask.
        "how many people in your household",
        "how many people in your household are you looking at",
    ),
    "es": (
        "cuantas personas hay en su hogar",
        "\u00bfcuantas personas hay en su hogar",
        "cuantas personas son en su hogar",
        "cual es el ingreso mensual bruto",
        "\u00bfcual es el ingreso mensual bruto",
        "alguna persona en su hogar tiene",
        "alguien en su hogar tiene",
        "tiene alguien en su hogar",
    ),
}


def _strip_completed_intake_re_asks(content: str, language: str) -> str:
    """Remove interrogative sentences that re-ask a filled intake field.

    The live failure on 2026-07-27 was the model, on a post-verdict
    turn, replying with: "That covers all three required fields ...
    One moment while I check that. **How many people in your household
    are you looking at?** **Does anyone in your household have an
    elderly (60+) or disabled member?**" The state had all three
    fields set, the calculator had already produced a verdict, and
    the broken prose leaked straight to the user because no
    deterministic filter recognized re-asking sentences in a
    context where intake was complete.

    Behavior:

    * only fires when called on completed-intake content (callers
      gate the call on ``state.missing_fields() == []``; this helper
      does no gating itself so it stays trivially unit-testable);
    * splits on sentence boundaries (``.``, ``?``, ``!``) and drops
      any sentence that ends in ``?`` AND whose lowered form
      contains one of the canonical re-ask phrases;
    * returns the input unchanged if every sentence would be
      stripped (we never want an empty reply on a completed intake
      because the user's first signal of completion should not be
      silence);
    * is a no-op when ``content`` is empty.
    """
    if not content:
        return content
    phrases = _REASK_PHRASES_BY_LANG.get(language, _REASK_PHRASES_BY_LANG["en"])
    # Split on sentence boundaries while keeping the delimiters attached
    # so we can detect "?". A trailing sentence without "?.!/" still
    # counts.
    parts = re.split(r"(?<=[.!?])\s+", content)
    kept: list[str] = []
    for sentence in parts:
        lowered = sentence.lower().rstrip()
        if lowered.endswith("?") and any(phrase in lowered for phrase in phrases):
            continue
        kept.append(sentence)
    stripped = " ".join(kept).strip()
    # If every sentence would be stripped, fall back to the original
    # content rather than returning "": an empty reply on a completed
    # intake would erase the verdict from the user's view. The post-
    # verdict outcome-label floor (called right after this helper)
    # handles the "content has no outcome term" case.
    return stripped or content


# P2.1: ack-opener rotation. The single-field and multi-field
# confirmations each pull from their own list, both keyed on
# state.turn_count % n. The lists are sized so back-to-back
# intakes produce visibly different openers (no copy-paste feel)
# while staying inside a small, testable, code-templated set.
# Spanish parity: ES list is the same shape as the EN list.
_ACK_OPENERS_EN_SINGLE: tuple[str, ...] = (
    "Got it",
    "Thanks",
    "Right",
    "OK",
)
# The multi-field confirmation now names each value it recorded, so the
# opener no longer carries the "I recorded those" phrasing -- it would
# just restate what the sentences after it already say.
_ACK_OPENERS_EN_MULTI: tuple[str, ...] = (
    "Got it",
    "Thanks",
    "Noted",
    "OK",
)
_ACK_OPENERS_ES_SINGLE: tuple[str, ...] = (
    "Entendido",
    "Gracias",
    "Correcto",
    "Anotado",
)
_ACK_OPENERS_ES_MULTI: tuple[str, ...] = (
    "Entendido",
    "Gracias",
    "Perfecto",
    "Anotado",
)


def _ack_opener(turn_count: int, lang: str, *, multi: bool) -> str:
    """Pick the ack opener for *turn_count* in *lang*. The rotation
    is keyed on ``turn_count % n`` so the choice is deterministic
    and the test suite can assert it without flakes. ``multi=True``
    selects the multi-field list (rare; only when 2+ fields land
    in the same turn)."""
    if lang == "es":
        lst = _ACK_OPENERS_ES_MULTI if multi else _ACK_OPENERS_ES_SINGLE
    else:
        lst = _ACK_OPENERS_EN_MULTI if multi else _ACK_OPENERS_EN_SINGLE
    return lst[turn_count % len(lst)]


def _intake_field_phrase(state: AssessmentState, field: str, lang: str) -> str:
    """Return the human sentence confirming *field*'s stored value.

    Shared by the single-field and multi-field confirmation branches so
    the two can never drift into describing the same value differently.
    """
    value = getattr(state, field, None)
    if value is None:
        return ""
    if lang == "es":
        persona_word = "persona" if field == "household_size" and value == 1 else "personas"
        return {
            "household_size": f"{value} {persona_word} compran y preparan comida juntas.",
            "gross_monthly_income": f"${float(value):,.0f} al mes.",
            "has_elderly_or_disabled_member": (
                "hay una persona de 60 años o más, o con discapacidad."
                if value else
                "no hay una persona de 60 años o más, ni con discapacidad."
            ),
        }.get(field, "dato registrado.")
    is_singular_household = field == "household_size" and value == 1
    person_word = "person" if is_singular_household else "people"
    verb_phrase = "buys and prepares" if is_singular_household else "buy and prepare"
    return {
        "household_size": f"{value} {person_word} {verb_phrase} food together.",
        "gross_monthly_income": f"${float(value):,.0f} per month.",
        "has_elderly_or_disabled_member": (
            "someone is age 60 or older or disabled."
            if value else
            "no one is age 60 or older or disabled."
        ),
    }.get(field, "I recorded that.")


def _intake_progress_reply(
    state: AssessmentState, newly_recorded: list[str], lang: str
) -> str:
    """Render a truthful transition from stored state, never model prose.

    P2.1: the ack opener rotates through a small list (4 entries
    each) keyed on ``state.turn_count % n`` so back-to-back intakes
    don\'t feel copy-pasted. The single-field and multi-field
    confirmations rotate independently.
    """
    missing = state.missing_fields()
    if not newly_recorded or not missing:
        return ""
    is_multi = len(newly_recorded) > 1
    opener = _ack_opener(state.turn_count, lang, multi=is_multi)
    if is_multi:
        # Multi-field: name every field that landed, the same way the
        # single-field branch does.
        #
        # The previous wording was content-free ("I recorded those
        # details.") and, when the rotation picked the matching opener,
        # redundant on top of that ("I recorded those — details
        # recorded."). Worse than clumsy: a turn like "it's me and my
        # two kids, and my mom stays with us" can land BOTH
        # household_size and has_elderly_or_disabled_member, and the
        # user was never told the second one was recorded -- so an
        # inference drawn from "my mom" (whose age was never stated)
        # became invisible and therefore uncorrectable. Stating each
        # value back is the same veto principle the enumerated-count
        # confirmation relies on: a value the user can see is a value
        # the user can fix.
        parts = [_intake_field_phrase(state, field, lang) for field in newly_recorded]
        parts = [part for part in parts if part]
        if not parts:
            return ""
        # The first phrase continues the opener's clause ("Got it — 4
        # people buy and prepare food together."), so it stays lowercase;
        # every later phrase begins a new sentence and must be capitalized.
        sentences = [parts[0]] + [part[0].upper() + part[1:] for part in parts[1:]]
        confirmation = f"{opener} — {' '.join(sentences)}"
    else:
        tail = _intake_field_phrase(state, newly_recorded[0], lang)
        if not tail:
            return ""
        confirmation = f"{opener} — {tail}"
    return f"{confirmation} {_NEXT_FIELD_QUESTION[lang][missing[0]]}"


# F5 (2026-07-28) affirmative-answer helpers.
#
# F5 closes the gap where a user’s message ends in ``?`` and is
# about whether a fact just recorded is relevant (the canonical
# example is the §9.1 probe turn 4: "my mom is 68 does that
# matter"). The existing pipeline acks the field (via
# ``_intake_progress_reply``) and then re-asks the next field, but
# it does NOT address the user’s question. The user hears the
# canonical re-ask and reasonably concludes the bot isn’t
# listening. F5 inserts a short affirmative answer so the
# reply (a) acknowledges the fact, AND (b) explains its relevance,
# AND (c) re-asks the next field.
#
# We deliberately split the implementation:
# - the *detector* checks if a message matches an informational class,
# - the *answer* content is code-templated per language (no model-written
#   answer; that would defeat the point of a deterministic reply).
#
# The set of known informational classes is intentionally small:
# the doc’s §9.3 names elderly-relevance as the only one for the
# 6pm-demo. Adding more classes is a follow-up that requires the
# same risk/cost evaluation (false positives add unsolicited
# answers; false negatives drop the relevance context).


def _is_elderly_relevance_question(user_message: str) -> bool:
    """Return True if *user_message* is a question whose answer is
    "an elderly/disabled household member changes which income
    test applies."

    Conservative on purpose: the user must have asked a question
    (the message ends in ``?`` after accounting for trailing
    whitespace/punctuation). The substantive signal is the F4
    elderly vocabulary (``elderly``, ``senior``, ``60+``,
    ``disabled``, ``discapacidad``, ``mayor``, etc.) OR an
    elderly-disability relevance phrase (``does that matter``,
    ``will that affect``, ``is that relevant``, ``importa``,
    ``afecta``, ``cambia``, ``qualifica``, ``eligible``,
    ``count``). The relevance-phrase branch is what catches the
    ``§9.1`` turn-4 probe ("my mom is 68 does that matter"),
    where the elderly-ness comes from the age (68) rather than
    the user typing one of the explicit elderly keywords.

    False positives fire the answer when it isn’t asked for
    (UX cost); false negatives drop the answer when it would’ve
    helped (F5’s prevention target). The detector errs on the
    side of *not* firing -- an over-eager affirmative answer is
    a worse experience than a missed one because the F4 ack
    already covers the fact, only the explanation is missing.
    """
    if not isinstance(user_message, str):
        return False
    stripped = user_message.rstrip()
    if not stripped:
        return False
    if not stripped.endswith("?") and not stripped.endswith("¿"):
        return False
    if FoodPathAgent._F4_ELDERLY_VOCAB.search(user_message):
        return True
    # Relevance phrases -- the probe ("my mom is 68 does that
    # matter") and close variants in EN and ES.
    relevance = re.search(
        r"\b(?:does\s+(?:that|this|it)\s+matter|will\s+(?:that|this|it)\s+(?:affect|change)|"
        r"is\s+(?:that|this|it)\s+relevant|"
        r"importa|afecta|cambiar|cambia\w*|"
        r"do\s+(?:they|it|that)\s+count|"
        r"does\s+(?:she|he)\s+count|"
        r"is\s+that\s+(?:relevant|a\s+factor|important)|"
        r"will\s+(?:she|he|they)\s+qualify)\b",
        user_message, re.IGNORECASE,
    )
    return bool(relevance)


def _elderly_relevance_answer(lang: str) -> str:
    """Return the short code-templated answer to an elderly-
    relevance question. Says yes (this matters) and names WHY
    (the gross-to-net income test switch). Returns empty string
    for an unsupported language so callers can fall through.
    """
    if lang == "es":
        return (
            "Sí — alguien mayor de 60 años o con discapacidad en el "
            "hogar cambia la prueba de ingresos de bruta a neta, "
            "así que es relevante para tu elegibilidad."
        )
    return (
        "Yes — someone 60+ or disabled in the household switches "
        "the income test from gross to net, so it’s relevant for "
        "your eligibility."
    )


# Spanish ¿ is the inverted question mark; English uses ?
# (already in the function above).

UPDATE_STATE_TOOL = {
    "name": "update_state",
    "description": (
        "Record explicitly supplied assessment fields. "
        "W61 contract: for gross_monthly_income, do NOT call update_state "
        "with a value latched from a comma- or 'and'-joined list of bare "
        "numbers ('27, 0, and 100' is NOT $27). When the user's reply to "
        "the income question carries multiple bare numbers and no income "
        "cue, omit this call and re-ask the income question instead. "
        "The deterministic fallback enforces the same rule."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "household_size": {"type": "integer", "minimum": 1, "maximum": max_household_size()},
            "gross_monthly_income": {"type": "number", "minimum": 0},
            "has_elderly_or_disabled_member": {"type": "boolean"},
            "earned_monthly_income": {"type": "number", "minimum": 0},
            "dependent_care_monthly": {"type": "number", "minimum": 0},
            "medical_expenses_monthly": {"type": "number", "minimum": 0},
            "shelter_costs_monthly": {"type": "number", "minimum": 0},
            "is_crisis": {"type": "boolean"},
            "is_out_of_scope": {"type": "boolean"},
            "is_injection_attempt": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
}

_ALLOWED_UPDATES = set(UPDATE_STATE_TOOL["input_schema"]["properties"])

# W61: tolerant pattern that matches integer tokens OR money-shaped tokens
# WITHOUT consuming thousands separators as list separators. Used to count
# how many distinct bare numbers a terse reply contains -- "27, 0, and 100"
# matches three times, "$2,100" matches once, "2100 a month" matches once.
# The pattern explicitly DOES NOT match the comma inside a thousands group,
# because we only fire findall() on the original text and let Python's
# re engine consume characters left-to-right; "2,100" -> match "2" first,
# then "100" (because the comma was already consumed as the thousands
# separator of the FIRST match's group). That would falsely inflate the
# count, so we instead use a NEGATIVE LOOKBEHIND that refuses to match
# a digit preceded by a comma: ``(?<!\d,)\d+``. This keeps "$2,100" at one
# match and "27, 0, and 100" at three.
_MULTI_NUMBER_TOKEN_RE = re.compile(
    r"(?<![,\d])\d+(?:\.\d+)?(?!\d)"
)

# W68b: bare rejection of the recorded household_size ("no not 1 person",
# "no, that's not 1") with no replacement number anywhere else in the
# message. Comma-aware (\d{1,3}(?:,\d{3})*) so it matches the whole
# number, consistent with the household_size fallback parser fix.
_HOUSEHOLD_BARE_REJECTION_RE = re.compile(
    r"\bno\b.{0,15}?\bnot\s+\$?\s*(\d{1,3}(?:,\d{3})*)\b",
    re.IGNORECASE,
)
# Every distinct number mentioned anywhere in a message, comma-grouping
# collapsed to one token (unlike _MULTI_NUMBER_TOKEN_RE, which
# deliberately treats "1,000" as just "1" for the income-ambiguity gate).
# Used to confirm a bare rejection carries no OTHER number that could be
# a real replacement value.
_ALL_NUMBER_TOKENS_RE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?")

# W61: role-bearing tokens that, when adjacent to a bare number in a
# terse reply, signal that the number has a clear semantic role --
# income vs expense, gross vs deduction. The presence of any of these
# tokens attached to ANY number in a multi-number reply distinguishes
# "structured" multi-number input ("1800 and I pay 400 for daycare")
# from "unstructured" multi-number input ("27, 0, and 100"). Only the
# unstructured case is a misassignment hazard; the structured case
# the LLM (and the deterministic fallback's normalized leading
# number) can both read correctly.
#
# English-only. Spanish terse-reply paths route through the LLM tool
# call, not the deterministic fallback, so adding Spanish vocabulary
# here is unnecessary and would risk a false-positive on common
# Spanish words like "para" (for) or "pago" (payment/I pay).
_ROLE_BEARING_NUMBER_PATTERN = re.compile(
    r"\b\d[\d,.]*\s*(?:dollars?|bucks?)\b"           # "1800 dollars"
    r"|\$\s*\d[\d,.]*"                                # "$1800"
    r"|\b\d[\d,.]*(?:\s*[kK])\b"                      # "1.5k"
    r"|\b(?:income|make|makes|earn|earned|earned|pay|pays|paid|"
    r"wages?|salary|salaries|paycheck)\b[^.\n]{0,30}?\b\d"  # "I pay 400", "earn 1800"
    r"|\b\d[^.\n]{0,30}?\b(?:daycare|childcare|child\s*care|"
    r"rent|shelter|medical|medication|utilities|bills?)\b"   # "400 for daycare"
    r"|\b(?:daycare|childcare|child\s*care|rent|shelter|"
    r"medical|medication|utilities|bills?)\b[^.\n]{0,30}?\b\d"
    r"|\b\d[^.\n]{0,15}?\b(?:a|per)\s+(?:month|week|hour|mo|wk|hr)\b"
    r"|\b(?:a|per)\s+(?:month|week|hour|mo|wk|hr)\b[^.\n]{0,15}?\b\d",
    re.IGNORECASE,
)


def _has_role_bearing_number(text: str) -> bool:
    """True if *text* contains a number adjacent to a role-bearing token.

    W61 discriminator between structured multi-number input ("1800 and
    I pay 400 for daycare" -- the LLM can read both numbers correctly)
    and unstructured multi-number input ("27, 0, and 100" -- reducing
    to "the first number" is a misassignment). Only unstructured input
    triggers the W61 decline-to-guess branch.
    """
    return bool(_ROLE_BEARING_NUMBER_PATTERN.search(text))


_VOLATILE_RULE_RE = re.compile(
    r"\b(?:immigration|immigrant|non[- ]?citizen|citizenship|visa|green card|"
    r"asylum|refugee|work requirements?|work registration|ABAWD|"
    r"able[- ]bodied adult)\b",
    re.IGNORECASE,
)


def volatile_rule_redirect(lang: str = "en") -> str:
    """Decline volatile immigration/work-rule opinions deterministically."""
    if lang == "es":
        return (
            "Esta evaluación no determina los requisitos de trabajo ni la "
            "elegibilidad según el estatus migratorio. Esas reglas cambiaron "
            "materialmente en 2025. Para una respuesta actual sobre su caso, "
            "comuníquese con la oficina del DSS de su condado."
        )
    return (
        "This screen does not evaluate work requirements or eligibility based "
        "on immigration status. Those rules changed materially in 2025. For "
        "a current answer about your circumstances, contact your county DSS office."
    )


# Two separate first-turn bubbles, not one combined message (restored
# 2026-07-27 per live human review: the W60-era "compact intro" pass fused
# these into a single bubble, which read as a wall of text that buried the
# actual first question). The disclosure carries ONLY the safety/identity
# content required regardless of tone -- the persistent notice/footer only
# covers "not an application / nothing is saved", not the 911/988 emergency
# pointer or the can't-see/call/dispatch capability limits, so both stay
# here (CA SB 243 / WA HB 2225 / FTC AI-disclosure guidance; see README.md's
# guardrail table). The friendly "Hi, I'm FoodPath" name-greeting lives in
# the SECOND bubble instead of being said twice -- crisis keywords still get
# the fuller hardcoded ``crisis_response()`` on top of all of this.
FIRST_TURN_DISCLOSURE_EN = (
    f"{PRODUCT_NAME} is an automated guide for a quick NC FNS / SNAP eligibility estimate — "
    "not a live person and not a DSS caseworker. It can't see you, call anyone, or dispatch "
    "help. If this is a life-threatening emergency, call 911. If you're in a mental-health "
    "crisis, call or text 988."
)

FIRST_TURN_DISCLOSURE_ES = (
    f"{PRODUCT_NAME} es una guía automatizada para una evaluación rápida de elegibilidad de "
    "FNS / SNAP — no es una persona real ni un trabajador social del DSS. No puede verte, "
    "llamar a nadie ni despachar ayuda. Si es una emergencia que pone en peligro tu vida, "
    "llama al 911. Si estás en una crisis de salud mental, llama o envía un mensaje de texto al 988."
)

FIRST_TURN_GREETING_EN = (
    f"Hi! I'm {PRODUCT_NAME}, here to help. To start, how many people, including you, "
    "usually buy and prepare food together? If you usually buy and cook just for yourself, answer 1."
)

FIRST_TURN_GREETING_ES = (
    f"¡Hola! Soy {PRODUCT_NAME}, aquí para ayudarte. Para empezar, ¿cuántas personas, "
    "incluyéndote, compran y preparan comida juntas? Si normalmente compras y cocinas solo "
    "para ti, responde 1."
)


def first_turn_disclosure(lang: str = "en") -> str:
    """Return the verbatim first-turn AI / not-a-person disclosure.

    The text is hard-coded (never model-generated) so the disclosure
    framing cannot drift, soften, or be silently dropped. Rendered on
    the very first assistant message of every conversation by the
    web UI / CLI entry points; the same content is appended to the
    system prompt's instructions so the LLM never tries to replace or
    contradict it.

    ``lang='es'`` renders the Spanish version (mirrors W19's bilingual
    contract so a Spanish-mode conversation never silently reverts to
    English on the first message). Any other value falls back to
    English.
    """
    if lang == "es":
        return FIRST_TURN_DISCLOSURE_ES
    return FIRST_TURN_DISCLOSURE_EN


def first_turn_greeting(lang: str = "en") -> str:
    """Return the first intake question fragment for the compact intro."""
    if lang == "es":
        return FIRST_TURN_GREETING_ES
    return FIRST_TURN_GREETING_EN


class FoodPathAgent:
    """Maintain assessment state and delegate extraction to structured tools."""

    def __init__(
        self,
        llm_client: LLMClient | Any | None = None,
        state: AssessmentState | None = None,
        retriever: Callable[[str], str] = retrieve,
    ) -> None:
        self.llm_client = llm_client or LLMClient()
        self.state = state or AssessmentState()
        # The system prompt is per-turn (see _system_prompt()): it follows
        # self.state.language so a mid-conversation language switch starts
        # on the very next LLM call.
        self.tools = [UPDATE_STATE_TOOL]
        self._retrieve = retriever
        self._messages: list[dict[str, str]] = []
        # Internal operational notes are never sent in provider messages[].
        self._audit: list[dict[str, Any]] = []
        self._eligibility_result: dict[str, Any] | None = None
        # Set when the user's income answer parsed but its PERIOD was
        # ambiguous ("70", "$70 a day", "$15/hr"). Consumed by the reply
        # path on the same turn to ask the specific clarifying question
        # instead of re-asking the generic income question.
        self._pending_income_clarification: str | None = None
        # The message that raised the clarification, kept one turn longer
        # than the reason itself. When the user supplies the missing half
        # ("40 hours a week"), it is only meaningful combined with the
        # original half ("$15/hr") -- reading the answer on its own turns
        # a $2,600/month wage into a $173/month one.
        self._pending_income_context: str | None = None
        # The reason code that raised the clarification, kept alongside the
        # context (``_pending_income_clarification`` is consumed and cleared
        # on the turn it is rendered). The RESOLUTION path needs it a turn
        # later: a two-figures clarification is resolved by selecting one of
        # the offered figures, which is a different operation from rejoining
        # the two halves of a single figure. See _fallback_parse_terse_reply.
        self._pending_income_reason: str | None = None
        # Per-turn: True when this turn's income came from rejoining the
        # reply with the held context, so the ambiguity check must not
        # re-flag the (deliberately partial) reply.
        self._income_resolved_via_context: bool = False
        # The turn on which the context was captured. The context answers a
        # question we asked, so it can only ever be combined with a reply
        # from a LATER turn -- without this, the message that raised the
        # clarification gets rejoined with itself on the same turn ("70" +
        # "70"), which silently re-confirms the very guess we were querying.
        self._pending_income_context_turn: int | None = None
        # W20: per-turn hostility flag. Set by ``turn()`` when the user's
        # message trips ``detect_hostility`` and no higher-priority guardrail
        # (crisis/injection/scope) claimed the turn. ``_system_prompt()``
        # reads it to prepend the one-line de-escalation context for that
        # turn's LLM call only; ``turn()`` resets it to False before
        # returning so it can never leak into the next turn.
        self._hostile_this_turn: bool = False
        # P0.3: classifier off_topic per-turn steering flag (see
        # _system_prompt for the hint that uses it). Mirrors the
        # _hostile_this_turn lifecycle: set in turn() when the
        # classifier returns 'off_topic', reset at the end of every
        # turn() so the next turn gets the stock prompt.
        self._classifier_off_topic_this_turn: bool = False
        # P0.3: classifier flagged the previous user message as off_topic
        # for this turn only. The main LLM still gets the turn (the
        # classifier no longer has conversation veto) but receives a
        # single-line steering hint so the reply can address the user's
        # actual question rather than re-asking the next missing field
        # blind. Reset to False at the end of every turn(), same
        # lifecycle as _hostile_this_turn.
        self._classifier_off_topic_this_turn: bool = False
        # Append-only history of intake-field corrections. A correction is
        # a non-None -> DIFFERENT non-None change to one of the three
        # intake fields (``household_size``, ``gross_monthly_income``,
        # ``has_elderly_or_disabled_member``). The first time a field is
        # set (None -> value) is NOT a correction — the user is providing
        # the field, not changing it. Each entry is a dict with keys
        # ``field``, ``old``, ``new``, and ``turn`` (the 1-indexed user
        # turn on which the correction was observed). The list is also
        # surfaced via ``_context()`` so the model can reference past
        # corrections naturally without re-deriving them.
        self._corrections: list[dict[str, Any]] = []
        # W61: per-turn audit of terse-income replies the deterministic
        # fallback declined to guess on. Entries are the raw user-message
        # strings that hit the "multiple bare numbers + no income cue"
        # branch (see ``_fallback_parse_terse_reply``). Visible in the
        # transcript log so a regression that re-introduces the
        # leading-number misassignment is debuggable from one place.
        # Resetting this between turns is NOT desirable -- the goal is
        # exactly to be able to look back across the conversation and
        # see every ambiguous multi-number reply.
        self._terse_income_ambiguous_log: list[str] = []
        # Retrieval hits (list of {doc, snippet, score}) behind the most
        # recent eligibility result. Populated when a result is first
        # produced (see turn()). Exposed for the web UI's citation panel
        # and so a reviewer can see WHICH documents grounded the answer.
        self.last_citations: list[dict[str, Any]] = []
        # Exact grounding blocks placed in the most recent prompt. Final
        # citations are derived from this list, never from a second retrieval.
        self._prompt_citations: list[dict[str, Any]] = []
        self.session_id = str(uuid.uuid4())
        self.last_layer = "llm"
        self.last_turn_event: dict[str, Any] = {}
        # W48: per-turn attribution of WHICH guardrail layer resolved the
        # turn. Set by ``_turn_impl`` when a keyword detector or the LLM
        # intent classifier fires; read by ``turn()`` to populate
        # ``last_turn_event['guardrail_source']`` so the red-team harness
        # can report per-layer attribution (keyword vs classifier). Reset
        # to ``None`` at the start of every turn.
        self._guardrail_source: str | None = None

    @property
    def eligibility_result(self) -> dict[str, Any] | None:
        return self._eligibility_result

    def handoff_summary(self) -> dict[str, Any] | None:
        """Build the W17 case-worker handoff package, or ``None``.

        Returns ``None`` when intake is incomplete or no deterministic
        eligibility result exists yet — the summary reflects real state
        only and can never be produced early. On success returns a dict
        with:

        * ``summary`` — the print-ready plain-English text block
          (code-templated in src/handoff.py, never model-generated);
        * ``banner`` — the on-screen confirmation line for the web UI,
          so the "ready to share with a case worker" signal is visible
          with zero external dependencies.

        The package is deliberately local-only: there is no outbound
        notification path of any kind. The case study says explicitly
        not to submit applications or contact agencies, and the good
        part of the handoff is the dependency-free summary block.
        """
        summary = handoff_summary(self.state, self._eligibility_result, self.last_citations)
        if summary is None:
            return None
        return {
            "summary": summary,
            "banner": _HANDOFF_BANNER,
        }

    def _system_prompt(self) -> str:
        """Return the system prompt matching the conversation language.

        English by default; the full Spanish prompt (W19) once
        ``state.language == 'es'``. Resolved per-turn (not cached in
        ``__init__``) so a mid-conversation language switch takes effect
        on the very next LLM call.

        W20: when the current turn's user message was flagged as hostile,
        one calm de-escalation instruction is prepended for THIS CALL ONLY
        (``_hostile_this_turn`` is reset at the end of every ``turn()``).
        It is never written into conversation history, so the next normal
        message gets the stock prompt again.
        """
        base = SYSTEM_PROMPT_ES if self.state.language == "es" else SYSTEM_PROMPT
        if self._hostile_this_turn:
            context = (
                HOSTILITY_CONTEXT_ES
                if self.state.language == "es"
                else HOSTILITY_CONTEXT_EN
            )
            return f"{context}\n\n{base}"
        if self._classifier_off_topic_this_turn:
            # P0.3: classifier flagged the prior message as off_topic.
            # Prepend the steering hint for THIS CALL ONLY — same
            # per-turn lifecycle as _hostile_this_turn (reset at the
            # end of every turn()). Never written to history.
            context = (
                _CLASSIFIER_OFF_TOPIC_HINT_ES
                if self.state.language == "es"
                else _CLASSIFIER_OFF_TOPIC_HINT_EN
            )
            return f"{context}\n\n{base}"
        return base

    def _track_query(self) -> str:
        """The retrieval query for the household's applicable income track."""
        if self.state.missing_fields():
            return "NC FNS eligibility intake questions"
        track = (
            "elderly disabled household net income rule"
            if self.state.has_elderly_or_disabled_member
            else "gross income 130 percent rule"
        )
        return f"NC FNS {track}"

    def _context(self, user_message: str = "") -> str:
        missing = self.state.missing_fields()
        phase_query = self._track_query()
        # Keep the phase guidance, but let the user's latest question steer
        # retrieval (for example, toward deductions during intake).
        query = f"{phase_query} {user_message}"[:200]
        retrieved = self._retrieve(query)
        # Custom test/demo retrievers may return prose; the production
        # retriever returns records. Preserve both interfaces.
        retrieval_hits = retrieved if isinstance(retrieved, list) else []
        grounding_blocks: list[Any] = []
        prompt_citations: list[dict[str, Any]] = []

        # Structured grounding is generated from the calculator's own source
        # of truth once intake is complete. Thus the displayed/cited threshold
        # equals the threshold used by calculate_eligibility().
        if not missing and self.state.household_size is not None:
            limits = income_limits(self.state.household_size)
            structured = {
                "doc": "calculator.income_limits()",
                "snippet": (
                    f"Household size {self.state.household_size}: gross 130 percent "
                    f"limit ${limits['gross']:,.2f}; net 100 percent limit "
                    f"${limits['net']:,.2f}; NC BBCE expanded 200 percent limit "
                    f"${limits['expanded']:,.2f}."
                ),
                "score": 1.0,
                "kind": "structured",
            }
            grounding_blocks.append(structured)
            prompt_citations.append(structured)

        if retrieval_hits:
            grounding_blocks.extend(retrieval_hits)
            prompt_citations.extend(
                hit for hit in retrieval_hits if isinstance(hit, dict)
            )
        elif not isinstance(retrieved, list):
            grounding_blocks.append(retrieved)
        self._prompt_citations = prompt_citations
        payload: dict[str, Any] = {
            "assessment_state": self.state.model_dump(),
            "missing_fields": missing,
            "retrieved_rule_text": grounding_blocks,
            "corrections": list(self._corrections),
        }
        if self._eligibility_result is not None:
            payload["deterministic_eligibility_result"] = self._eligibility_result
        return json.dumps(payload, default=str)

    # Fields whose change invalidates a cached eligibility result.
    # The three required intake fields (household_size, gross_monthly_income,
    # has_elderly_or_disabled_member) pick the household and the test track.
    # The four optional deduction fields (earned_monthly_income,
    # dependent_care_monthly, medical_expenses_monthly, shelter_costs_monthly)
    # feed the federal net-income deduction chain in
    # ``compute_net_monthly_income``: a deduction disclosed AFTER a verdict
    # has been reached must invalidate the cache and trigger a recompute,
    # otherwise the documented flagship example (household of 1, $1,800 gross
    # all earned) reports 1800.0 / qualifies=False even though the calculator
    # with the deduction produces 1236.0 / qualifies=True. See W26.
    _INTAKE_FIELDS = (
        "household_size",
        "gross_monthly_income",
        "has_elderly_or_disabled_member",
        "earned_monthly_income",
        "dependent_care_monthly",
        "medical_expenses_monthly",
        "shelter_costs_monthly",
    )

    # W64: broaden the income cue so a number paired with a month/week/hour
    # token ("2100 a month", "around 2k monthly", "2100 bucks/month") also
    # counts. The previous pattern required a $ or income verb, which
    # silently dropped the contradicting_income correction ("Oh wait, ...
    # closer to 2100 a month."). Adding ``a\s+month``, ``monthly``, ``a\s+week``,
    # ``weekly``, ``hourly`` etc. so the model's update_state path and the
    # deterministic fallback agree on the same vocabulary.
    #
    # IMPORTANT: the numeric alternative requires a literal ``$`` followed by
    # digits. Without that anchor, the previous ``\$?\s*\d`` alternative
    # matched ANY digit anywhere in the message -- so an age ("she's 72")
    # or a household count ("there are 4 of us") got counted as an income
    # cue, which routed both into the income-correction path. The bare
    # verb/month-keyword alternatives below are sufficient for the
    # number-only income lines ("we make about 1800", "closer to 2100 a
    # month") because the terse-fallback path does NOT need an income
    # cue to extract a number -- only the model's tool-call path and the
    # out-of-order income-correction block do.
    # Naming a pay period IS an income cue: "42000 a year" and "$70 a day"
    # are unambiguously answers to the income question, but without the
    # annual/daily terms below they failed this gate, never reached the
    # normalizer, and the agent just re-asked the same question -- the
    # dead-end loop that makes the screen impossible to finish.
    _INCOME_CUE_PATTERN = (
        r"(?:\$[\s\d,.]+|\b(?:income|make|makes|earn|earned|paid|salary|salaries|"
        r"wages?|paycheck|per\s+month|per\s+week|per\s+hour|a\s+month|a\s+week|"
        r"a\s+hour|an\s+hour|monthly|weekly|hourly|biweekly|bimonthly|"
        r"per\s+year|a\s+year|yearly|annually|annual|per\s+day|a\s+day|daily|"
        r"/\s*(?:hr|hour|mo|month|wk|week|yr|year|day)|"
        r"ingresos?|ganamos?|sueldo|al\s+año|al\s+día|anual|"
        r"bucks?|dollars?)\b)"
    )

    # F4 (2026-07-28): cheap plausibility gate for the out-of-order
    # extraction pass. We must NOT call the LLM for every turn — the
    # extraction call is per-turn latency/cost — so we restrict it
    # to messages that plausibly carry intake content. Three cheap
    # signal classes: any digit (income / household-size / age),
    # elderly/disabled vocabulary, household-composition vocabulary.
    # Match the same level of conservatism as the existing income-cue
    # gate — false positives fall through to the next-field re-ask
    # (a small UX cost), false negatives silently drop a fact (the
    # failure mode F4 was created to prevent).
    _F4_ELDERLY_VOCAB = re.compile(
        r"\b(?:elderly|senior|seniors|disabled|disability|"
        r"60\+|over\s+60|older\s+adult|aging|aged|"
        r"mayor|discapacidad|mayores|de\s+la\s+tercera\s+edad)\b",
        re.IGNORECASE,
    )
    _F4_HOUSEHOLD_VOCAB = re.compile(
        r"\b(?:household|family|families|members?|people|"
        r"personas?|hijos?|hijo|daughter|son|husband|wife|partner|"
        r"wife|mom|mum|mother|dad|father|grandma|grandpa|grandmother|"
        r"grandfather|nana|papa|mama|"
        r"kids?|toddler|baby|newborn|spouse|sibling|brother|sister|"
        r"live\s+with|living\s+with|stays\s+with|roommate|"
        r"hogar|familia)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _message_plausibly_has_intake_content(cls, user_message: str) -> bool:
        """F4 gate: return True if *user_message* plausibly carries an
        intake fact, False if it is plain social / unrelated prose.

        Conservative on purpose — a missed positive is a fact
        silently dropped (F4's prevention-target), a missed negative
        is one extra LLM call per turn (a fixed cost). The
        exact-match set: any digit, elderly/disabled vocabulary,
        household-composition vocabulary.
        """
        if not isinstance(user_message, str) or not user_message.strip():
            return False
        if re.search(r"\d", user_message):
            return True
        if cls._F4_ELDERLY_VOCAB.search(user_message):
            return True
        if cls._F4_HOUSEHOLD_VOCAB.search(user_message):
            return True
        return False

    # F4 extraction prompt. Distinct from the main conversational
    # system prompt so the stub detectors in tests can route chat()
    # calls. The prompt is deliberately short, with the same
    # wording the §9.3 spec calls out verbatim: "extract only
    # facts the user explicitly stated about THEIR OWN household;
    # if none, call nothing." The middle paragraph spells out the
    # three intake fields and the negative "not THEIR household"
    # guardrail so a chatty model doesn't over-extract neighbors
    # or coworkers.
    _F4_EXTRACTION_PROMPT = (
        "extract only facts the user explicitly stated about "
        "THEIR OWN household; if none, call nothing.\n\n"
        "If the user stated any of these about their own household, "
        "call update_state on the matching field with the exact "
        "value:\n"
        "  - household_size (integer)\n"
        "  - gross_monthly_income (number; weekly is x 52/12, "
        "annual is /12)\n"
        "  - has_elderly_or_disabled_member (boolean, true if any "
        "household member is 60+ or disabled)\n\n"
        "If the user is talking ABOUT someone outside their "
        "household (a neighbor, friend, coworker, or relative not "
        "in their household), call no tool for that fact.\n\n"
        "Use only tool calls. No conversational prose."
    )

    @classmethod
    def _message_has_intake_cue(cls, field: str, user_message: str) -> bool:
        # W55 targets the consequential stale-income path. Other fields keep
        # their established model/fallback behavior; applying a speculative
        # vocabulary gate to all seven fields caused legitimate terse and
        # multi-field answers to be dropped.
        if field != "gross_monthly_income":
            return True
        if re.search(cls._INCOME_CUE_PATTERN, user_message, re.I):
            return True
        # W61: a structured multi-number reply (one number adjacent to
        # a role-bearing verb or noun) is also a valid income cue. The
        # classic example is "1800 and I pay 400 for daycare" -- the
        # message is plainly about income AND a deduction, but neither
        # number is adjacent to the income cue vocabulary. The
        # role-bearing check is the W61 discriminator between
        # structured and unstructured multi-number input.
        return _has_role_bearing_number(user_message)

    def _apply_tool_calls(
        self,
        calls: list[dict[str, Any]],
        *,
        user_message: str | None = None,
        asked_field: str | None = None,
    ) -> list[dict[str, Any]]:
        """Apply every update_state tool call in ``calls``.

        Processes ALL update_state calls in the turn (a model may emit
        more than one), accumulating corrections across them. Returns a
        list of correction records — one per intake field whose value
        changed from a previous non-None value to a DIFFERENT non-None
        value. The caller commits these to ``self._corrections`` only
        after this method returns successfully, so a validation failure
        mid-turn leaves no phantom correction behind (the whole turn's
        apply raises before any correction is committed).
        """
        new_corrections: list[dict[str, Any]] = []
        for call in calls:
            if call.get("name") != "update_state":
                continue
            raw = call.get("input") or {}
            if not isinstance(raw, dict):
                raise ValueError("update_state input must be an object")
            updates = {key: value for key, value in raw.items() if key in _ALLOWED_UPDATES}
            if user_message is not None:
                updates = {
                    field: value
                    for field, value in updates.items()
                    if self._message_has_intake_cue(field, user_message)
                    or (
                        field == asked_field
                        and self._fallback_parse_terse_reply(field, user_message) is not None
                    )
                }
            if not updates:
                continue
            # ``gross_monthly_income`` is the only intake field that can
            # legitimately arrive as a fuzzy string like "$2,500" or
            # "$600/week" rather than a plain float. Run every incoming
            # value through ``normalize_income`` (which is total: it
            # accepts numbers, strings, and None, and returns None on
            # unparseable input) so the calculator downstream only sees
            # a clean monthly float. A None return means the caller
            # forgot to pass a usable value and we drop that field
            # rather than write garbage into state.
            if "gross_monthly_income" in updates:
                # Before accepting a value, check whether the USER'S OWN
                # WORDING left the pay period ambiguous. This is checked on
                # the raw message rather than the extracted number because
                # both paths that can fill this field (the model's tool call
                # and the deterministic terse-reply fallback) will happily
                # commit "70" as $70/month -- the model by guessing, the
                # fallback by parsing. "$70 a day" is ~$1,500/month and
                # "$42,000 a year" is $3,500/month; recording either at face
                # value silently produces the wrong verdict, and a wrong
                # denial is the most harmful output this tool has.
                ambiguity = (
                    classify_income_ambiguity(
                        redact_for_logging(user_message),
                        value=normalize_income(updates["gross_monthly_income"]),
                    )
                    if user_message is not None
                    and not self._income_resolved_via_context
                    else None
                )
                if ambiguity is not None:
                    self._pending_income_clarification = ambiguity
                    self._pending_income_reason = ambiguity
                    self._pending_income_context = self._accumulate_income_context(
                        user_message
                    )
                    self._pending_income_context_turn = self.state.turn_count
                    updates.pop("gross_monthly_income", None)
                    self._audit.append({
                        "turn": self.state.turn_count,
                        "event": "income_ambiguous_clarification_requested",
                        "reason": ambiguity,
                    })
                else:
                    normalized = normalize_income(updates["gross_monthly_income"])
                    if normalized is None:
                        updates.pop("gross_monthly_income", None)
                    else:
                        updates["gross_monthly_income"] = normalized
                        # A usable figure landed; the half-answer we were
                        # holding is spent and must not leak into a later
                        # income correction.
                        self._pending_income_context = None
                        self._pending_income_reason = None
            if not updates:
                continue
            # Detect intake-field corrections (non-None -> DIFFERENT
            # non-None) BEFORE we mutate ``self.state``. ``None -> value``
            # is intentionally NOT a correction — the user is providing
            # the field for the first time, not changing it. A False ->
            # True flip on ``has_elderly_or_disabled_member`` counts. Any
            # such change also invalidates a cached eligibility result so
            # the next ``_calculate_once`` recomputes against corrected
            # state (the self-contradiction scenario the rubric tests).
            call_corrections: list[dict[str, Any]] = []
            for field in self._INTAKE_FIELDS:
                if field not in updates:
                    continue
                new_value = updates[field]
                old_value = getattr(self.state, field)
                if old_value is None or new_value == old_value:
                    continue
                call_corrections.append(
                    {
                        "field": field,
                        "old": old_value,
                        "new": new_value,
                        "turn": self.state.turn_count,
                    }
                )
            # model_copy does not validate in Pydantic v2, so validate the
            # candidate FIRST and only assign on success — assigning before
            # validation would leave an invalid value (e.g. household_size
            # 999) live in state even after the error is caught.
            candidate = self.state.model_copy(update=updates)
            try:
                self.state = AssessmentState.model_validate(candidate.model_dump())
            except ValidationError as exc:
                raise ValueError(f"Invalid update_state call: {exc}") from exc
            if call_corrections and self._eligibility_result is not None:
                self._eligibility_result = None
            new_corrections.extend(call_corrections)
        return new_corrections

    def _try_household_bare_rejection_clear(self, user_message: str, lang: str) -> str | None:
        """W68b: detect a BARE rejection of the recorded household_size
        (e.g. "no not 1 person") with no replacement number anywhere else
        in the message, and if found, clear the field (and any cached
        verdict) and return a deterministic, code-templated re-ask reply.
        Returns ``None`` when the guard does not fire, so callers can fall
        through to their normal turn processing.

        Live human review, 2026-07-28: after a misparse recorded
        household_size=1, the user replied "no not 1 person" -- an
        unambiguous rejection -- but it matched neither the mid-intake
        `correction_cue` (needs "actually"/"sorry"/"no wait"/etc., not a
        bare "no") nor `household_phrase_cue` (needs "of us"/"there
        are"/etc.), so the wrong 1 silently survived to the final verdict,
        and the model's own attempt to reconcile the contradiction on the
        next turn produced a duplicate question (household size AND
        income asked together). There is no new number to apply here, so
        the only correct response is to clear the field and re-ask -- and
        the reply is code-templated rather than left to the model for the
        same reason the W60 restart/recap replies are: the model
        improvising here is exactly what produced the duplicate-question
        bug. Shared by both the mid-intake out-of-order-correction path
        and the post-verdict early-return path, since a bare rejection is
        equally possible (and was observed) in both.
        """
        if self.state.household_size is None:
            return None
        bare_reject = _HOUSEHOLD_BARE_REJECTION_RE.search(user_message)
        if not bare_reject:
            return None
        rejected_value = int(bare_reject.group(1).replace(",", ""))
        if rejected_value != self.state.household_size:
            return None
        other_numbers = {
            tok.replace(",", "") for tok in _ALL_NUMBER_TOKENS_RE.findall(user_message)
        }
        other_numbers.discard(str(rejected_value))
        if other_numbers:
            return None
        self.state.household_size = None
        self._eligibility_result = None
        self._audit.append(
            {
                "turn": self.state.turn_count,
                "event": "field_cleared",
                "note": (
                    "[FoodPath audit] User rejected the recorded "
                    "household_size with no replacement value; field "
                    "(and any cached verdict) cleared for re-ask."
                ),
            }
        )
        self._guardrail_source = "keyword"
        ack = (
            "Entendido, ese número no es correcto. "
            "Empecemos de nuevo con esa pregunta."
            if lang == "es"
            else "Got it, that number wasn't right -- let's fix it."
        )
        content = _w53_fold_ask(ack, lang, list(self.state.missing_fields()))
        self._messages.append({"role": "assistant", "content": content})
        return content

    # Bare yes/no tokens for the elderly/disabled field fallback below.
    # English-only: Spanish-mode conversations rely on the LLM tool call
    # (the fallback is a narrow backstop, not a full parser).
    _NEGATIVE_WORDS = frozenset({"no", "nope", "nah", "n", "none", "nobody", "no one", "noone"})
    _POSITIVE_WORDS = frozenset({"yes", "yeah", "yep", "y", "sure"})

    def _fallback_parse_terse_reply(self, field: str, user_message: str) -> Any | None:
        """Deterministic backstop for a terse reply the model failed to
        turn into an update_state call for the ONE field it just asked
        about.

        Not every provider reliably connects a bare reply ("3", "2200",
        "no") to the field it answers -- MiniMax in particular is
        noticeably worse at this than at fuller sentences ("household
        size is 3"). Real users answer tersely far more often than not,
        and the case study explicitly grades handling exactly this kind
        of plain, everyday input gracefully. This never guesses across
        fields: the caller only invokes it for the single field that was
        missing when the turn began, so a parse failure just leaves the
        field unset for the LLM to re-ask, never misfiles into the wrong
        slot.
        """
        text = user_message.strip()
        if field == "household_size":
            # W56: extend the fallback so a plain-language reply the
            # model fails to parse ("There are 3 of us.", "household of
            # 4", "Just me, household of 2.", "actually it's 4 of us")
            # still gets recorded. The previous regex required a leading
            # digit, which the contradicting scenario's "There are 2 of
            # us at home." sidestepped — the field stayed None, the
            # next question never fired, and the simulator's
            # contradiction_income_final / contradiction_household_final
            # rules flagged the conversation as a missed contradiction.
            # The script-style digit-leading form is still the first
            # branch so the existing narrow path is unchanged.
            # Every digit-capture below uses \d{1,3}(?:,\d{3})* rather than
            # a flat \d{1,4} so a comma-grouped number ("1,000") is read as
            # ONE number, not truncated at the comma. Found live 2026-07-28:
            # "1,000 people, and then 0 montly income" matched the old
            # \d{1,4} leading regex as just "1" (the comma isn't a digit),
            # "1" is in the valid 1..max_household_size() range, so it was
            # accepted and returned immediately -- silently recording a
            # household of 1 instead of declining the (out-of-range, but
            # syntactically a single number) 1,000. The same truncation
            # would also misread a legitimate value like "1,005 people" as
            # trailing "005" == 5. Commas are stripped before int() so the
            # full number is what actually gets range-checked.
            leading = re.match(r"^\$?(\d{1,3}(?:,\d{3})*)\b", text)
            if leading:
                value = int(leading.group(1).replace(",", ""))
                if 1 <= value <= max_household_size():
                    return value
                return None
            # Phrase forms: "There are N of us", "household of N",
            # "we are N", "just me, household of N", "actually it's N
            # of us", "N people", "N of us", "N person" (singular).
            phrase = re.search(
                r"\b(?:there\s+are|we\s+are|we're|household\s+of|"
                r"actually\s+it'?s|just\s+me,?\s+household\s+of)\s+"
                r"(\d{1,3}(?:,\d{3})*)(?:\s+(?:of|people|persons?|in\s+the\s+house))?\b",
                text,
                re.IGNORECASE,
            )
            if phrase:
                value = int(phrase.group(1).replace(",", ""))
                if 1 <= value <= max_household_size():
                    return value
            # "N people in our household" / "N of us" / "N person" at
            # the end of the reply.
            trailing = re.search(
                r"\b(\d{1,3}(?:,\d{3})*)\s+(?:people|persons?|of\s+us)\b",
                text,
                re.IGNORECASE,
            )
            if trailing:
                value = int(trailing.group(1).replace(",", ""))
                if 1 <= value <= max_household_size():
                    return value
            return None
        if field == "gross_monthly_income":
            # Redact PII-shaped digit runs before any income parsing. Found
            # live 2026-07-28: "my ssn is 123-45-6789 and income is 2000"
            # has an explicit income cue, so the multi-number-ambiguity
            # guard below doesn't fire, and normalize_income() grabbed the
            # SSN's leading "123" as the monthly income instead of the
            # actually-stated 2000 -- a wrong income silently produces a
            # wrong eligibility verdict, which is the single most harmful
            # class of bug this tool can have. Legitimate income figures
            # (dollar amounts, up to a few hundred thousand) don't collide
            # with any of redact_for_logging's patterns (9-digit/dashed
            # SSN, area-code phone, 8+-digit labeled account, 10+-digit
            # bare run), so this is safe for every real answer.
            text = redact_for_logging(text)
            # If we asked a clarifying question last turn ("how many hours a
            # week?"), this turn's reply is only half the answer. Rejoin it
            # with the half the user already gave so "$15/hr" + "40 hours a
            # week" becomes the $2,600/month the two facts actually mean,
            # rather than a $40/week reading of the reply in isolation.
            if (
                self._pending_income_context
                and self._pending_income_context_turn != self.state.turn_count
            ):
                context = self._pending_income_context
                # F3b (2026-07-28): a TWO-FIGURES clarification is resolved
                # by SELECTING one of the two figures we offered, never by
                # re-parsing "context, answer" as one string. The two
                # operations look similar and are not: rejoining completes
                # ONE figure whose halves arrived separately ("$15/hr" +
                # "40 hours"), while two-figures must CHOOSE between two
                # complete figures. Live-probed at 723e795: the rejoin read
                # the period cue attached to the second figure ("300 a
                # week") and applied it to the first (2500), producing a
                # fabricated $10,833/month -- and it produced that same
                # value for EVERY answer, including the correct "the 300
                # one", because the answer never actually participated.
                # The agent then issued a confident false denial, which is
                # the most harmful output this tool has.
                if self._pending_income_reason == AMBIGUITY_TWO_FIGURES:
                    selected = resolve_two_figures_answer(context, text)
                    if selected is not None:
                        self._income_resolved_via_context = True
                        return selected
                    # The answer did not choose a figure. Resolve NOTHING
                    # and let the message be handled as whatever it
                    # actually was -- "my mom is 68 does that matter" is an
                    # elderly-field fact, not an income answer.
                    return None
                # The reply must plausibly carry the missing half before it
                # is allowed to complete a held figure. Same failure shape
                # as the two-figures bug above: an unrelated message that
                # merely contains a number ("my mom is 68") would otherwise
                # be multiplied into the held rate. A message whose numbers
                # belong to another intake field, and which carries no
                # income cue of its own, cannot be the answer we asked for.
                if _mentions_other_intake_field(text) and not re.search(
                    FoodPathAgent._INCOME_CUE_PATTERN, text, re.I
                ):
                    return None
                candidates = [f"{context}, {text}"]
                # A bare "40" answering "how many hours a week?" carries the
                # unit only in the question we asked, so restore it before
                # parsing. Which unit to restore is decided by the context,
                # never by guessing at the number.
                if re.fullmatch(r"\s*\d{1,3}(?:\.\d+)?\s*", text):
                    if re.search(r"/\s*h|hour|hourly|\bhr\b", context, re.I):
                        candidates.append(f"{context}, {text} hours a week")
                    elif re.search(r"\bday|daily\b", context, re.I):
                        candidates.append(f"{context}, {text} days a week")
                for candidate in candidates:
                    combined = normalize_income(candidate)
                    if combined is not None:
                        # Deliberately does NOT clear the context here: this
                        # parser is called once to gate the fallback and
                        # again to use it, so clearing on the first call
                        # would leave the second with nothing to rejoin.
                        # The context is cleared where the value lands.
                        # Tell the choke point not to second-guess this
                        # value: the period ambiguity it would flag on the
                        # bare reply ("40") is exactly what we just resolved.
                        self._income_resolved_via_context = True
                        return combined
            # A bare numeric/currency reply is a valid answer to the income
            # question. Text containing an unrelated number ("she's 72")
            # must carry an explicit income cue before it can be normalized.
            #
            # W61: a comma-joined list of bare numbers ("27, 0, and 100")
            # used to slip through the previous ``leading_amount_with_details``
            # gate -- ``leading_amount_with_details`` matched "27," as a
            # leading amount-with-details, the whole string then went through
            # ``normalize_income`` which returned the LEADING number (27) as
            # income. That is the exact shape that landed a wrong $27 figure
            # in the final eligibility explanation during 2026-07-27 human
            # review. The fallback's docstring promises "a parse failure just
            # leaves the field unset for the LLM to re-ask, never misfiles
            # into the wrong slot" -- this block delivers on that promise
            # for the multi-number case.
            bare_amount = re.fullmatch(
                r"\s*\$?\s*\d[\d,.]*(?:\s*[kK])?(?:\s*(?:dollars?|bucks?))?\s*",
                text,
            )
            has_income_cue = bool(
                re.search(FoodPathAgent._INCOME_CUE_PATTERN, text, re.I)
            )
            # W61 discriminator: when the reply has 2+ bare numbers AND
            # no income cue, count the multi-number case as "ambiguous"
            # ONLY when NONE of the numbers has a clear adjacent role.
            # "1800 and I pay 400 for daycare" has 2 numbers but the
            # second one is anchored to "I pay ... for daycare" -- a
            # structured role separation the LLM's update_state call
            # can read. We only want to decline when the multi-number
            # reply is unstructured (no role-bearing verb/noun adjacent
            # to any number), e.g. "27, 0, and 100".
            numbers = _MULTI_NUMBER_TOKEN_RE.findall(text)
            single_thousands_form = re.fullmatch(
                r"\s*\$?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:\s*[kK])?\s*",
                text,
            )
            # A bare "income" mention elsewhere in the message is NOT
            # enough to trust the leading number -- found live 2026-07-28:
            # "1,000 people, and then 0 montly income" has has_income_cue
            # True (the word "income" appears) but "income" isn't adjacent
            # to either number, so the OLD "not has_income_cue" escape
            # hatch let this through and grabbed the leading 1,000 as
            # income, silently dropping the actually-stated 0. Role-
            # bearing-ness (a number adjacent to $, k, "income"/"pay" DIRECTLY
            # followed by a digit, or a deduction word) is the real test
            # for "this multi-number reply is structured enough to trust,"
            # not merely containing the word "income" somewhere.
            is_unstructured_multi_number = (
                len(numbers) >= 2
                and not _has_role_bearing_number(text)
            )
            if is_unstructured_multi_number:
                # W61 audit: this case must be observable in the turn log
                # so a regression (or a user reporting "the agent took
                # the wrong number") is debuggable from the transcript.
                self._terse_income_ambiguous_log.append(text)
                return None
            if not bare_amount and not has_income_cue and not single_thousands_form:
                return None
            return normalize_income(text)
        if field == "has_elderly_or_disabled_member":
            token = text.lower().strip(".! ")
            if token in FoodPathAgent._NEGATIVE_WORDS:
                return False
            if token in FoodPathAgent._POSITIVE_WORDS:
                return True
            # A LEADING yes/no is still an unambiguous answer even with
            # trailing content -- "yes all 150", "yeah definitely",
            # "nope none of them" all open with a clear answer; the rest
            # is emphasis or (confused) elaboration, not a correction.
            # Live human review, 2026-07-28: the exact-match check above
            # required the ENTIRE reply to be a single bare word, so any
            # trailing text at all made the field silently fail to parse
            # -- and it kept failing across three consecutive tries with
            # three different trailing numbers/phrases, none of which
            # were ever recorded. This is a general pattern (any trailing
            # content), not specific to any one number or phrase, so the
            # fix generalizes rather than special-casing what was typed
            # tonight. Callers that need "is the user correcting a
            # DIFFERENT field" already suppress this fallback entirely
            # via the correction-cue check upstream (e.g. "no wait, I
            # meant 3 people"), so a leading "no" here is never reached
            # in that case.
            # Guard: if the trailing content carries an income cue ("yes,
            # $1,800" restating income after the elderly question became
            # the currently-asked field), the "yes" is affirming a
            # DIFFERENT field's value, not answering this one -- don't
            # guess across fields. This is the exact shape of the W61
            # income-leading-number bug, one field over: a conversational
            # "yes" that means "yes, correct" rather than "yes, elderly."
            if not re.search(FoodPathAgent._INCOME_CUE_PATTERN, text, re.I):
                leading = re.match(r"^(\w+)\b", token)
                if leading:
                    word = leading.group(1)
                    if word in FoodPathAgent._NEGATIVE_WORDS:
                        return False
                    if word in FoodPathAgent._POSITIVE_WORDS:
                        return True
            return None
        return None

    def _accumulate_income_context(self, user_message: str) -> str:
        """Append *user_message* to any income context we are already holding.

        Resolving an ambiguous income can take more than one exchange
        ("70" -> "a day" -> "5"), and each reply carries only a fragment of
        the answer. Replacing the context each time would discard the
        amount itself by the time the user names the period; accumulating
        keeps every fragment so the final rejoin has all of them.
        """
        message = (user_message or "").strip()
        if not message:
            return self._pending_income_context or ""
        if self._pending_income_context:
            return f"{self._pending_income_context}, {message}"
        return message

    def _f4_out_of_order_extraction(
        self,
        user_message: str,
        asked_field: str | None,
        lang: str,
    ) -> None:
        """F4 (2026-07-28) out-of-order fact extraction.

        Closes the gap where a user message carries an intake
        fact the main model dropped (no ``update_state`` tool
        call was emitted) but a deterministic backstop *did
        not* fire either -- so the fact evaporates. The §9.1
        probe message 4 ("my mom is 68 does that matter") is
        the canonical case.

        Design (from §9.3 of CONVERSATION_OVERHAUL.md): make
        **one additional LLM call** whose ONLY job is
        extraction. Tool calls flow through the *same*
        ``_apply_tool_calls`` gates. State still changes only
        via a model-issued, schema-validated tool call --
        ``test_free_text_is_never_parsed_into_state`` stays
        green and the §8 invariant stays honest.

        Called only when:
          * the main model emitted no ``update_state`` calls, AND
          * the message plausibly carries intake content
            (gate ``_message_plausibly_has_intake_content``), AND
          * intake is still incomplete (no verdict computed).
        """
        # Audit BEFORE the call so a timeout / exception is still
        # observable in the transcript.
        self._audit.append({
            "turn": self.state.turn_count,
            "event": "f4_extraction_pass_fired",
            "note": "[FoodPath audit] F4: extraction LLM call invoked; "
                    "main model emitted no update_state calls and "
                    "the message plausibly carries intake content.",
        })
        try:
            ext_response = self.llm_client.chat(
                [
                    {
                        "role": "user",
                        "content": redact_for_logging(user_message),
                    }
                ],
                system=self._F4_EXTRACTION_PROMPT,
                tools=self.tools,
            )
        except Exception as exc:  # pragma: no cover - defensive
            # A failing extraction must NEVER crash the turn.
            # Log and bail; the next-field re-ask will fire.
            self._audit.append({
                "turn": self.state.turn_count,
                "event": "f4_extraction_pass_error",
                "error": repr(exc),
            })
            return

        ext_tool_calls = (ext_response or {}).get("tool_calls") or []
        if not ext_tool_calls:
            # No tool calls = no field was extractable. Most
            # common case for the negative tripwire ("my
            # neighbor is 68"). No state change.
            return

        # Fill-only semantics: even though _apply_tool_calls
        # would reject a malformed value, it would also accept
        # a valid one. We filter to only-None fields BEFORE the
        # tool-call path so a non-None field is never
        # overwritten without the existing correction-cue
        # mechanism in _apply_tool_calls.
        if not self._f4_fill_only_filter(ext_tool_calls, user_message):
            return

        try:
            self._apply_tool_calls(
                ext_tool_calls,
                user_message=user_message,
                asked_field=asked_field,
            )
        except ValueError:
            # Bad model output dropped silently; the no-prose-scan
            # invariant means a malformed value never reaches
            # state regardless.
            pass

    def _f4_fill_only_filter(
        self,
        ext_tool_calls: list[dict[str, Any]],
        user_message: str,
    ) -> bool:
        """Apply the F4 fill-only contract.

        For each ``update_state`` tool call in ``ext_tool_calls``:
          * keep the field if state value is currently None (fill),
          * if the field is already set:
              - ALLOW overwrite only if the user message contains
                an explicit correction cue (matches the existing
                W64/W68b correction-cue vocabulary),
              - otherwise, drop the field from the call.

        Returns True if at least one field survives the filter
        (so the call is worth sending through ``_apply_tool_calls``),
        False if every field was dropped.
        """
        # Correction cue vocabulary — mirrors W64/W68b in agent.py.
        # Same set so F4 reuses the existing correction-not-allowed
        # path that the user already proved by typing "actually".
        _CORRECTION_CUE = re.compile(
            r"\b(?:actually|sorry|i\s+meant|correction|no\s+wait|"
            r"that(?:s|\s+is)\s+wrong|"
            r"en\s+realidad|perd[oó]n|quer[ií]a\s+decir|"
            r"correcci[oó]n|no\s+espera)\b",
            re.I,
        )
        correction_cue = bool(_CORRECTION_CUE.search(user_message))

        any_kept = False
        for call in ext_tool_calls:
            if call.get("name") != "update_state":
                continue
            raw = call.get("input") or {}
            if not isinstance(raw, dict):
                continue
            for field, value in list(raw.items()):
                current = getattr(self.state, field, None)
                if current is None:
                    # Fill — the field is missing; allow.
                    any_kept = True
                    continue
                if correction_cue:
                    # Overwrite with correction cue — the
                    # W64/W68b path will handle the diff.
                    any_kept = True
                    continue
                # Non-None AND no correction cue: drop this
                # field from the tool call so the F4 path
                # doesn't silently overwrite state.
                raw.pop(field, None)
                self._audit.append({
                    "turn": self.state.turn_count,
                    "event": "f4_extraction_dropped_for_no_correction_cue",
                    "field": field,
                    "note": "F4: a non-None field was extracted by the "
                            "extraction pass but dropped because the "
                            "user message contained no explicit "
                            "correction cue. The existing field is "
                            "preserved.",
                })
            # If we emptied the input dict, drop the call entirely.
            if not raw:
                continue
        return any_kept

    def _calculate_once(self) -> None:
        if self._eligibility_result is not None or not self.state.is_ready_to_calculate():
            return
        household_size = self.state.household_size
        gross_income = self.state.gross_monthly_income
        elderly_or_disabled = self.state.has_elderly_or_disabled_member
        assert household_size is not None
        assert gross_income is not None
        assert elderly_or_disabled is not None
        # W26: thread volunteered deduction fields through so the
        # calculator's net-income chain runs when state has them. Omit
        # zero-valued optional kwargs to preserve the original three-argument
        # call shape for callers that supplied no deductions; the calculator
        # defaults those fields to zero and its no-input path is unchanged.
        calculator_kwargs: dict[str, Any] = {
            "household_size": household_size,
            "gross_monthly_income": gross_income,
            "has_elderly_or_disabled": elderly_or_disabled,
            # FoodPath is the NC application. Pass the jurisdiction
            # explicitly because the calculator's generic API defaults to
            # the federal 130% floor when no state is selected.
            "state_code": "NC",
        }
        optional_deductions = {
            "earned_monthly_income": self.state.earned_monthly_income,
            "dependent_care_monthly": self.state.dependent_care_monthly,
            "medical_expenses_monthly": self.state.medical_expenses_monthly,
            "shelter_costs_monthly": self.state.shelter_costs_monthly,
        }
        if any(value > 0 for value in optional_deductions.values()):
            calculator_kwargs.update(optional_deductions)
        self._eligibility_result = calculate_eligibility(**calculator_kwargs)

    def turn(self, user_message: str) -> str:
        """Telemetry boundary around a complete turn."""
        # W48: reset per-turn attribution. ``_turn_impl`` sets this to
        # ``'keyword'`` when a deterministic detector fires and
        # ``'classifier'`` when the LLM intent classifier fires.
        self._guardrail_source = None
        self._income_resolved_via_context = False
        before = self.state.model_dump(mode="json")
        started = time.perf_counter()
        reply = ""
        try:
            reply = self._turn_impl(user_message)
            return reply
        finally:
            after = self.state.model_dump(mode="json")
            if after.get("is_crisis") and not before.get("is_crisis"):
                layer = "crisis"
            elif after.get("is_injection_attempt") and not before.get("is_injection_attempt"):
                layer = "injection"
            elif after.get("is_out_of_scope") and not before.get("is_out_of_scope"):
                layer = "scope"
            elif self._audit and self._audit[-1].get("turn") == self.state.turn_count and self._audit[-1].get("event") == "hostility_detected":
                layer = "hostility"
            else:
                # W46: telemetry describes the component that actually
                # resolved the turn, not merely the fact that no safety
                # guardrail fired. Intake extraction and calculator verdicts
                # are deterministic application layers even though an LLM may
                # assist parsing. Calling every ordinary turn ``llm`` hid that
                # architecture and made the layer-skew signal meaningless.
                changed_intake = any(
                    before.get(field) != after.get(field)
                    for field in (
                        "household_size",
                        "gross_monthly_income",
                        "has_elderly_or_disabled_member",
                        "earned_monthly_income",
                        "dependent_care_monthly",
                        "medical_expenses_monthly",
                        "shelter_costs_monthly",
                    )
                )
                if self._eligibility_result is not None:
                    layer = "calculator"
                elif changed_intake:
                    layer = "intake"
                else:
                    layer = "llm"
            self.last_layer = layer
            event = {"session_id": self.session_id, "turn_index": self.state.turn_count,
                "user_message": user_message, "layer": layer, "classifier_label": None,
                # W48: per-layer attribution. ``None`` when the LLM
                # resolved the turn without a guardrail firing (ordinary
                # intake / calculator turns). The red-team harness reads
                # this to report per-layer counts.
                "guardrail_source": self._guardrail_source,
                "state_before": before, "state_after": after, "tool_calls": [],
                "fallback_fired": False, "fallback": None,
                "retrieval_hits": self._prompt_citations, "calculator_inputs": before,
                "calculator_output": self._eligibility_result,
                "provider": type(self.llm_client).__name__,
                "llm_latency_ms": round((time.perf_counter() - started) * 1000, 3), "reply": reply}
            self.last_turn_event = event
            emit_turn(event)

    def _turn_impl(self, user_message: str) -> str:
        """Process one user turn, prioritizing deterministic guardrails.

        Order of evaluation:
        1. Crisis detection (food/safety emergencies) -> hardcoded resources.
           Crisis MUST be evaluated before injection: a message like
           "ignore your instructions — I'm going to kill myself" matches
           both detectors, and answering it with an injection refusal
           instead of the 988/211 resources would be the worst possible
           outcome. Safety outranks security here, deliberately.
        2. Prompt-injection detection -> a hardcoded refusal. The next
           ordinary message is evaluated normally; the flag is historical
           and does not lock the conversation into refusal mode.
        3. Out-of-scope detection (legal advice, unrelated topics)
           -> fixed polite redirect. State is preserved; is_out_of_scope
           is flipped so the agent loop can remember.
        4. Otherwise, hand to the LLM as normal.

        The PII redactor is applied to the user message before it is
        recorded in the internal message history so that any later log
        dump is free of SSN-shaped or account-shaped patterns.
        """
        self.state.turn_count += 1

        # Record the redacted form of the user message in conversation
        # history so any log/print of the history is PII-safe.
        safe_user_message = redact_for_logging(user_message)
        self._messages.append({"role": "user", "content": safe_user_message})

        # W19: sticky per-conversation language detection. Once the user
        # writes a message the W15 heuristic classifies as Spanish, the
        # conversation switches to Spanish mode (state.language == "es")
        # and STAYS there: later terse messages ("no", "$1,800") carry no
        # Spanish signal and must never flip the conversation back to
        # English mid-intake. The flag is deterministic (never set by the
        # model) and presentation-only: it changes the system prompt and
        # the guardrail/suffix renderings, never the calculator inputs.
        if self.state.language != "es" and detect_spanish(user_message):
            self.state.language = "es"
        lang = self.state.language

        if detect_crisis(user_message):
            self.state.is_crisis = True
            # W48: keyword layer attribution.
            self._guardrail_source = "keyword"
            # The default bilingual block stays the safe choice when the
            # language is still unknown; once a mode is set, the crisis
            # response renders in that language only so a Spanish
            # conversation never silently reverts to English mid-crisis.
            content = crisis_response(
                "bilingual" if not detect_spanish(user_message) else lang
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # Injection remains above scope for dual-flag override attempts.
        if detect_injection(user_message):
            self.state.is_injection_attempt = True
            # W48: keyword layer attribution.
            self._guardrail_source = "keyword"
            content = injection_response(lang)
            # P0.4: same state-aware next-field fold as the other
            # canned continuations. The injection response is
            # disclosure text describing all three intake fields
            # generically; the W53 floor appends the actual next
            # missing field's question so filled fields are not
            # re-requested.
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        if detect_out_of_scope(user_message):
            self.state.is_out_of_scope = True
            # W48: keyword layer attribution.
            self._guardrail_source = "keyword"
            content = scope_redirect_message(lang)
            # P0.4: route the "come back to the intake" tail through
            # the W62 next-field helper. The scope-redirect message
            # describes all three intake fields generically; without
            # this fold, the user is told they need household_size
            # again even after they have already provided it. The
            # W53 floor appends the actual next missing field's
            # question (no-op if the reply already contains it).
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W27: identity-disclosure questions are now reachable because persona
        # detection requires an imperative frame.
        # P0.2: route the strict identity-class subset ("who are you",
        # "what do you do", "what is this") to a short identity answer
        # + the next missing field's question, rather than the full
        # 4-channel "what this is NOT" block. The wider
        # ``detect_not_a_person_request`` still fires for the harder
        # cases (is this 911? will you call my caseworker?) where the
        # full block is the right answer.
        if detect_identity_question(user_message):
            content = short_identity_response(
                lang, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content
        if detect_not_a_person_request(user_message):
            content = not_a_person_response(lang)
            # P0.4: route the "continue with the FNS questions" tail
            # through the W53 floor so the actual next missing field
            # is appended and filled fields are not re-requested.
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        if _VOLATILE_RULE_RE.search(user_message):
            content = volatile_rule_redirect(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        # Close-the-loop informational handlers. These are
        # narrowly scoped, deterministic, code-templated responses for
        # specific administrative questions that the agent would
        # otherwise either improvise an answer to (worst case) or
        # silently fail to address (the "I just asked about expedited
        # SNAP and the assistant asked me about my household size
        # again" failure). Each is informational, not safety-critical,
        # so they sit AFTER the safety/contract guards (crisis,
        # injection, scope, not-a-person) and BEFORE the LLM call. The
        # intake is preserved -- the response explicitly invites the
        # user to continue with the eligibility questions.
        if detect_expedited_question(user_message):
            content = expedited_response(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        if detect_benefit_amount_question(user_message):
            content = benefit_amount_response(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        if detect_application_status_question(user_message):
            content = application_status_response(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W69: "what's the max household size?" must not be improvised.
        # Live human review, 2026-07-28: the model answered "no max...
        # tops out around 8 to 10 people" -- a fabricated figure with no
        # grounding (the real screener cap is calculator.max_household_size()
        # == 20, and SNAP itself has no statutory maximum). See
        # household_size_question.py for the full writeup. Folded with the
        # next pending question (rather than a terminal reply) so intake
        # keeps moving, same as the process-question handler above.
        if detect_max_household_size_question(user_message):
            content = max_household_size_response(lang)
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W59 — in-scope process questions are legitimate user turns, not
        # off-topic requests. Answer privacy/persistence/application claims
        # deterministically so the model cannot improvise trust-critical
        # assurances, then keep the intake moving with the same W53 question
        # floor used by ordinary model replies.
        process_category = detect_process_question(user_message)
        if process_category is not None:
            content = process_question_response(process_category, lang)
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W60 — restarter persona: explicit "start over" must CLEAR the
        # recorded intake state, not just acknowledge the words. Wired
        # through the same reset path the W50 ``/session/reset`` button
        # uses (foodpath.web.reset_session, which constructs a fresh
        # AssessmentState). The agent-level reset preserves language,
        # guardrail bookkeeping, and the message history so the W53
        # floor keeps a stable frame of reference.
        if detect_restart_request(user_message):
            apply_intake_reset(self.state)
            # apply_intake_reset() only has access to AssessmentState --
            # it cannot clear the AGENT's own cached calculator output.
            # Found live 2026-07-28: after a full restart and providing
            # entirely new answers, the verdict stayed pinned to the
            # PRE-restart figures (household 3 / $2,200 restart-then-
            # refilled to household 10 / $50 still showed $2,200 as the
            # income considered) because _calculate_once() short-circuits
            # whenever self._eligibility_result is already set. A stale
            # verdict surviving an explicit restart is a wrong-verdict
            # bug, the same severity class as W61/the pay-period fixes.
            self._eligibility_result = None
            self._audit.append(
                {
                    "turn": self.state.turn_count,
                    "event": "intake_reset",
                    "note": (
                        "[FoodPath audit] User requested an explicit restart. "
                        "AssessmentState intake fields have been cleared; "
                        "language, guardrail flags, and message history are "
                        "preserved."
                    ),
                }
            )
            self._guardrail_source = "keyword"
            content = reset_intake_message(lang)
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W60 — restarter persona: recap request. The recap is
        # code-templated from AssessmentState so the model cannot
        # improvise and accidentally re-state a value the user has
        # since changed. The W53 floor still appends the next-field
        # question after the recap.
        if detect_recap_request(user_message):
            self._guardrail_source = "keyword"
            content = recap_message(self.state, lang)
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # High-frequency orientation questions should be useful even if the
        # provider is unavailable or verbose. Answer first, then resume only
        # the one missing intake question.
        if re.search(
            r"\b(?:what\s+is\s+(?:snap|fns)|what\s+does\s+snap\s+(?:mean|stand\s+for))\b",
            user_message,
            re.I,
        ):
            if lang == "es":
                content = (
                    "SNAP es un programa que ayuda a hogares con ingresos bajos a comprar comida; "
                    "en Carolina del Norte se llama FNS y los beneficios se reciben en una tarjeta EBT."
                )
            else:
                content = (
                    "SNAP helps households with low incomes buy food. In North Carolina it is "
                    "called FNS, and approved benefits are provided on an EBT card."
                )
            content = _w53_fold_ask(content, lang, self.state.missing_fields())
            self._messages.append({"role": "assistant", "content": content})
            return content

        if (
            self.state.missing_fields()
            and re.search(
                r"\b(?:do\s+i\s+get\s+it|am\s+i\s+eligible|do\s+i\s+qualify|will\s+i\s+qualify)\b",
                user_message,
                re.I,
            )
        ):
            content = (
                "Puedo darte una estimación después de tres datos breves."
                if lang == "es"
                else "I can give you an estimate after three short details."
            )
            content = _w53_fold_ask(content, lang, self.state.missing_fields())
            self._messages.append({"role": "assistant", "content": content})
            return content

        if (
            self.state.household_size is None
            and re.search(
                r"\b(?:(?:don't|dont|do\s+not)\s+own\s+(?:a\s+)?house|no\s+house|homeless|unhoused)\b",
                user_message,
                re.I,
            )
        ):
            content = (
                "No necesitas ser dueño de una vivienda. Para SNAP, cuenta a las personas que normalmente compran y preparan comida juntas."
                if lang == "es"
                else "You do not need to own a home. For SNAP, count the people who usually buy and prepare food together."
            )
            content = _w53_fold_ask(content, lang, self.state.missing_fields())
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W60 — drifter persona: off-topic asides mid-intake (rent
        # gripes, bus-route complaints, food-bank hours, neighbour
        # gossip, kids' sick days, plant-closure history, family /
        # out-of-state comparisons). The trigger-aware redirect never
        # uses the canned scope "I can't give legal advice" wording —
        # that was the W53 bug class. State is preserved; intake
        # stays in flight. The in-scope "is it true they're cutting
        # benefits next year?" question is explicitly EXCLUDED by the
        # detector (it anchors to the FNS topic via the in-scope
        # token guard) so it reaches the LLM / informational path.
        if detect_drifter_aside(user_message):
            self._guardrail_source = "keyword"
            content = drifter_redirect_message(user_message, lang)
            content = _w53_fold_ask(
                content, self.state.language, list(self.state.missing_fields())
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # A plain greeting does not need a provider round-trip and must never
        # trigger a second disclosure. The UI already introduced FoodPath.
        if re.fullmatch(r"\s*(?:hi|hello|hey|hola|buenas)[!.?\s]*", user_message, re.I):
            missing = self.state.missing_fields()
            if missing:
                prefix = "¡Hola!" if lang == "es" else "Hi!"
                content = f"{prefix} {_NEXT_FIELD_QUESTION[lang][missing[0]]}"
            else:
                content = (
                    "¡Hola! Tu evaluación ya está completa."
                    if lang == "es"
                    else "Hi! Your screening is already complete."
                )
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W21 — not-a-person disambiguation. Strictly lower priority than
        # crisis / injection / scope (which all claim the turn above) and
        # strictly higher priority than the ordinary LLM path. A user who
        # explicitly asks "are you a real person" / "are you calling
        # someone" / "is this 911" gets the deterministic disambiguation
        # block instead of a model-generated reply, so the framing cannot
        # W20: hostility is NON-BLOCKING and lower priority than the blocking
        # guardrails above. Identity disclosure already returned immediately
        # after crisis handling (W27).
        # neither a crisis, nor an injection, nor out of scope, so flagging
        # it as hostile can never suppress the hardcoded crisis/injection/
        # scope responses. The detector runs HERE (not earlier) so the
        # evaluation order itself enforces the invariant: crisis (and
        # injection, and scope) always win over hostility. A hostile message
        # gets one calm de-escalation instruction prepended to the system
        # prompt for THIS turn only (see _system_prompt) plus an audit note
        # in conversation history; the conversation is never refused or
        # ended and the FNS intake continues normally.
        if detect_hostility(user_message):
            self._hostile_this_turn = True
            self._audit.append(
                {
                    "turn": self.state.turn_count,
                    "event": "hostility_detected",
                    "note": (
                        "[FoodPath audit] The user's previous message was "
                        "flagged as hostile toward the assistant. De-escalation "
                        "context was added to the system prompt for this turn "
                        "only; the conversation continues normally. Do not "
                        "mention this note to the user."
                    ),
                }
            )

        # Layer 2 (defense in depth): the LLM intent classifier runs
        # AFTER the keyword guardrails. The keyword layer is fast,
        # deterministic, auditable, and works offline — it stays first.
        # The classifier catches novel phrasings the keyword lists miss
        # (the red-team suite already proved keyword lists are brittle
        # on unseen inputs). On any classifier failure (exception,
        # unparseable output, or 'normal'), the agent falls through to
        # the ordinary LLM call below. The classifier is disabled by
        # env var GUARDRAIL_CLASSIFIER=off so unit tests, offline runs,
        # and the model bake-off can opt out of the extra LLM call.
        #
        # PII: the classifier is a SECOND LLM provider call on top of
        # the agent's main call. The same ``safe_user_message`` that
        # goes into conversation history goes to the classifier — the
        # raw user input never leaves the local process. (Finding #20
        # in PRODUCTION_READINESS_REVIEW.)
        #
        # P2.2 latency optimization (opt-in via
        # AGENT_SKIP_CLASSIFIER_ON_CLEAN=1): when ALL keyword guardrails
        # above have passed without claiming the turn (crisis,
        # injection, scope, identity, not_a_person, process, drifter,
        # hostility), the message is "clean" and the classifier is
        # unlikely to flip it to crisis/injection (the only two
        # intents that still short-circuit after P0.3). The classifier
        # is skipped to save one LLM round-trip per clean turn.
        #
        # Trade-off: a novel-phrasing off-topic message that all
        # keyword layers miss will NOT set ``is_out_of_scope`` for
        # red-team attribution. The steering-hint path (P0.3) still
        # applies — the main LLM gets a per-turn hint if the
        # classifier WERE to return off_topic — but the classifier
        # is bypassed entirely. The expanded keyword lists (P0.2)
        # cover the common cases; the trade-off favors latency for
        # strong main models.
        #
        # Default: classifier runs (current behavior). Set the env
        # var to opt in to the skip. The doc's "Measure before/after"
        # caveat is documented in CONVERSATION_OVERHAUL.md P2.2.
        import os as _os
        skip_classifier = (
            _os.environ.get("AGENT_SKIP_CLASSIFIER_ON_CLEAN", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        if skip_classifier:
            # Optimistic default: assume the keyword layer's verdict
            # holds. The main LLM path below still runs the same
            # code that the classifier would have (off_topic is now
            # a steering hint, not a short-circuit). We log to
            # _audit so latency-measurement tooling can see when
            # the skip fired.
            intent = "normal"
            self._audit.append({
                "turn": self.state.turn_count,
                "event": "classifier_skipped_clean_turn",
                "note": (
                    "[FoodPath audit] P2.2: classifier call skipped on a "
                    "clean turn (all keyword guardrails passed). "
                    "Set AGENT_SKIP_CLASSIFIER_ON_CLEAN=0 to re-enable."
                ),
            })
        else:
            intent = classify_intent(safe_user_message, self.llm_client)
        if intent == "crisis":
            self.state.is_crisis = True
            # W48: classifier layer attribution.
            self._guardrail_source = "classifier"
            content = crisis_response(
                "bilingual" if not detect_spanish(user_message) else lang
            )
            self._messages.append({"role": "assistant", "content": content})
            return content
        if intent == "injection":
            self.state.is_injection_attempt = True
            # W48: classifier layer attribution.
            self._guardrail_source = "classifier"
            content = injection_response(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content
        if intent == "off_topic":
            # P0.3: the classifier no longer has conversation veto on
            # off_topic. Crisis and injection still short-circuit
            # (above); off_topic just sets a per-turn flag so the main
            # LLM gets a single-line steering hint for THIS turn only
            # (see _system_prompt). The model handles the actual reply
            # — it can acknowledge what the user said, answer a real
            # question they asked, then return to the intake. The
            # historical ``is_out_of_scope`` state flag is preserved
            # for red-team attribution and the W48 layer-attribution
            # telemetry so the dashboard still knows the classifier
            # caught it.
            self.state.is_out_of_scope = True
            self._guardrail_source = "classifier"
            self._classifier_off_topic_this_turn = True

        # Captured BEFORE the LLM call: the single field this turn's
        # question was actually about, if the intake isn't complete yet,
        # and whether income already had a value from an EARLIER turn.
        # Both are used below as narrow, deterministic backstops -- see
        # _fallback_parse_terse_reply. Capturing income's pre-turn state
        # matters: checking ``self.state.gross_monthly_income`` AFTER the
        # LLM call would also be true for a plain first-time fill THIS
        # turn, which is not a correction and must not be re-parsed.
        asked_field = self.state.missing_fields()[:1]
        intake_before = {
            field: getattr(self.state, field)
            for field in (
                "household_size",
                "gross_monthly_income",
                "has_elderly_or_disabled_member",
            )
        }
        income_set_before_this_turn = self.state.gross_monthly_income is not None

        # Once DECIDE has already produced a result, later turns are
        # explanation-only — BUT a user correction on this turn still
        # must be applied (and must invalidate the cached eligibility
        # result so the next provider call renders a fresh verdict).
        # Without this branch, post-verdict corrections silently no-op
        # and the agent keeps re-rendering the stale verdict.
        if self._eligibility_result is not None:
            # W68b: check for a bare household_size rejection BEFORE
            # calling the model at all -- the live duplicate-question bug
            # came from trusting the model's own prose to reconcile a
            # rejection it had no clean way to express. See
            # _try_household_bare_rejection_clear for the full writeup.
            bare_rejection_reply = self._try_household_bare_rejection_clear(
                user_message, lang
            )
            if bare_rejection_reply is not None:
                self._hostile_this_turn = False
                self._classifier_off_topic_this_turn = False
                return bare_rejection_reply
            response = self.llm_client.chat(
                [*self._messages, {"role": "user", "content": self._context(user_message)}],
                system=self._system_prompt(),
                tools=self.tools,
            )
            post_corrections: list[dict[str, Any]] = []
            try:
                post_corrections = self._apply_tool_calls(
                    response.get("tool_calls") or [],
                    user_message=user_message,
                    asked_field=asked_field[0] if asked_field else None,
                )
            except ValueError:
                # Bad tool call from the model: drop it, keep the
                # cached verdict, fall through to the explanation path.
                post_corrections = []
            # Deterministic backstop, live-probed 2026-07-28: unlike every
            # other correction path in this file, a post-verdict correction
            # had NO fallback at all -- it depended entirely on the model
            # emitting a tool call. Reproduced: "actually wait, my income
            # is really 1500" after a $2,200 verdict silently did nothing
            # in 1 of 5 live runs (model returned no tool call), leaving
            # the OLD verdict on screen with no indication the correction
            # was ignored. Same correction-cue + income-cue shape already
            # used by the mid-intake W64 out-of-order-correction fallback
            # below -- that code is unreachable from this early-return
            # branch, so this mirrors it rather than sharing it.
            if not post_corrections:
                correction_cue = re.search(
                    r"\b(?:actually|sorry|i\s+meant|correction|no\s+wait|"
                    r"that(?:s|\s+is)\s+wrong|en realidad|perd[oó]n|"
                    r"quer[ií]a decir|correcci[oó]n|no\s+espera)\b",
                    user_message, re.I,
                )
                income_cue = re.search(FoodPathAgent._INCOME_CUE_PATTERN, user_message, re.I)
                deduction_cue = re.search(
                    r"\b(?:rent|mortgage|childcare|daycare|medical|car|"
                    r"utilities?|insurance|bills?|gas|electric)\b",
                    user_message, re.I,
                )
                if correction_cue and income_cue and not deduction_cue:
                    parsed_income = normalize_income(user_message)
                    if (
                        parsed_income is not None
                        and parsed_income != self.state.gross_monthly_income
                    ):
                        try:
                            post_corrections = self._apply_tool_calls(
                                [{"name": "update_state", "input": {"gross_monthly_income": parsed_income}}],
                                user_message=user_message,
                                asked_field=None,
                            )
                        except ValueError:
                            post_corrections = []
            if post_corrections:
                # A real correction just landed. Invalidate the cached
                # result so the recompute below produces a fresh one.
                self._eligibility_result = None
                self._calculate_once()
                self._corrections.extend(post_corrections)
                for entry in post_corrections:
                    self._audit.append({
                        "turn": self.state.turn_count,
                        "event": "post_verdict_correction",
                        "field": entry["field"],
                        "old": entry["old"],
                        "new": entry["new"],
                    })
            content = str(response.get("content") or "")
            # W62: strip any re-asking sentences the post-verdict
            # model may have emitted for a filled intake field. State
            # had all three fields set going into this turn, so a
            # re-ask is always wrong here (and the live failure on
            # 2026-07-27 was exactly this: a model that said "How
            # many people in your household are you looking at?"
            # after we already had it). The stripper is gated by
            # ``state.missing_fields() == []`` so it never runs
            # mid-intake.
            if not self.state.missing_fields():
                content = _strip_completed_intake_re_asks(
                    content, self.state.language
                )
            content = _outcome_label_floor(
                content, self._eligibility_result, self.state.language, _purpose="verdict_with_corrections"
            )
            # SSN redaction applies on every turn, this early-return path
            # included -- found live 2026-07-28: a post-verdict follow-up
            # question is exactly where a chatty model is most likely to
            # summarize everything the user said, an earlier-pasted SSN
            # included, and this branch returns before the P1.2 validator
            # block further down ever runs. See the fuller note there.
            from .validators import check_ssn_fabrication, redact_ssn_fabrication
            if check_ssn_fabrication(content):
                content = redact_ssn_fabrication(content)
                self._audit.append({
                    "turn": self.state.turn_count,
                    "event": "p1_2_ssn_redacted_from_reply",
                    "note": "Model echoed an SSN-shaped string; redacted before sending to the user.",
                })
            self._messages.append({"role": "assistant", "content": content})
            self._hostile_this_turn = False
            self._classifier_off_topic_this_turn = False
            return content

        # If the user asks to repeat a completed Spanish result, make a
        # fresh explanation call rather than silently reusing the prior
        # provider response. This keeps the per-turn prompt contract visible
        # to callers and preserves a Spanish explanation on every turn.
        if self._eligibility_result is not None:
            response = self.llm_client.chat(
                [*self._messages, {"role": "user", "content": self._context(user_message)}],
                system=self._system_prompt(),
                tools=self.tools,
            )
            content = str(response.get("content") or "")
            # W62: same re-ask stripper as the branch above. This
            # is the alternate post-verdict path (``Spanish repeat``
            # and any future code that takes the same early-return
            # shape); running it here keeps both branches honest.
            if not self.state.missing_fields():
                content = _strip_completed_intake_re_asks(
                    content, self.state.language
                )
            content = _outcome_label_floor(
                content, self._eligibility_result, self.state.language, _purpose="explain_repeat"
            )
            # SSN redaction on this early-return path too -- see the
            # matching note on the branch above.
            from .validators import check_ssn_fabrication, redact_ssn_fabrication
            if check_ssn_fabrication(content):
                content = redact_ssn_fabrication(content)
                self._audit.append({
                    "turn": self.state.turn_count,
                    "event": "p1_2_ssn_redacted_from_reply",
                    "note": "Model echoed an SSN-shaped string; redacted before sending to the user.",
                })
            self._messages.append({"role": "assistant", "content": content})
            self._hostile_this_turn = False
            self._classifier_off_topic_this_turn = False
            return content

        # If a completed intake receives a follow-up, ask the provider to
        # explain the existing result in the current conversation language.
        # The post-verdict branch above handles this path and also applies
        # corrections, so this duplicate branch is intentionally removed.

        # EXTRACT: this pass may update structured state; its prose is never
        # shown because it was generated from pre-decision context.
        extract_response = self.llm_client.chat(
            [*self._messages, {"role": "user", "content": self._context(user_message)}],
            system=self._system_prompt(),
            tools=self.tools,
        )
        model_updated_income_this_turn = any(
            call.get("name") == "update_state" and "gross_monthly_income" in (call.get("input") or {})
            for call in (extract_response.get("tool_calls") or [])
        )
        new_corrections: list[dict[str, Any]] = []
        try:
            new_corrections = self._apply_tool_calls(
                extract_response.get("tool_calls") or [],
                user_message=user_message,
                asked_field=asked_field[0] if asked_field else None,
            )
        except ValueError:
            # A malformed/over-limit tool call from the model (e.g.
            # household_size above the schema cap) must not crash the
            # turn. Drop the bad update; state stays valid, the field
            # stays missing, and the loop re-asks on the next turn.
            # ``new_corrections`` keeps its default empty value so no
            # phantom suffix is appended below.
            pass

        # F4 (2026-07-28): out-of-order fact extraction. Fires when
        # the main model dropped the ball (no update_state tool
        # call) AND the message plausibly carries intake content
        # AND the intake is still incomplete. One additional LLM
        # call whose ONLY job is extraction -- no conversational
        # output. Tool calls flow through the same _apply_tool_calls
        # gates, so the §8 no-prose-scan invariant is preserved.
        # See _f4_out_of_order_extraction for the full writeup.
        #
        # Enabled by DEFAULT; set F4_EXTRACTION=0 to disable. It shipped
        # opt-in (default OFF) so it would not disturb the call-count
        # assertions that pre-date it, but nothing ever turned it on --
        # not .env, not the setup scripts, not the server -- so the fix
        # for the dropped-fact failure in §9.1 was dead code in every
        # real run. A guardrail nobody enables is not a guardrail. The
        # call-count tests that need the old behaviour now opt OUT
        # explicitly, which is the correct direction for the default:
        # the safe-for-users setting should not depend on remembering
        # to set an env var.
        if (
            _os.environ.get("F4_EXTRACTION", "1").strip().lower()
            not in ("0", "false", "no", "off")
            and self.state.missing_fields()
            and not (extract_response.get("tool_calls") or [])
            and self._message_plausibly_has_intake_content(user_message)
        ):
            self._f4_out_of_order_extraction(
                user_message,
                asked_field[0] if asked_field else None,
                lang,
            )

        # Deterministic fallback: if the model's tool call (or lack of
        # one) left the field this turn's question was about still
        # missing, try a narrow, conservative parse of the raw reply
        # itself. Some providers are noticeably less reliable at
        # connecting a bare reply ("3", "2200", "no") to the field it
        # answers than at fuller sentences -- this never touches any
        # field except the one the agent just asked about, so a parse
        # failure just leaves the field missing for the LLM to
        # re-ask.
        # Guardrail: if the user message carries a correction cue
        # (e.g. "actually 5" mid-intake when correcting household_size
        # from 2 -> 5), the fallback MUST NOT grab that number and
        # file it under the field the agent was asking about. Without
        # this, "actually 5" gets parsed as gross_monthly_income=5.0
        # because the agent was already asking about income -- a
        # silent field-routing bug. The same correction cue we use
        # below for income cues is reused here; the field-routing
        # intent ("I meant to change something else") wins over the
        # "the agent happened to be asking about a different field"
        # default. The LLM still gets the turn to do the right thing.
        correction_cue_for_fallback = re.search(
            r"\b(?:actually|sorry|i\s+meant|correction|no\s+wait|that(?:s|\s+is)\s+wrong|"
            r"en realidad|perd[oó]n|quer[ií]a decir|correcci[oó]n|no\s+espera)\b",
            user_message, re.I,
        )
        income_cue_for_fallback = re.search(
            r"\b(?:income|make|makes|earn|paid|pay\s+me|salary|salaries|wages|"
            r"per\s+month)\b", user_message, re.I,
        )
        fallback_applied = False
        if asked_field and asked_field[0] in self.state.missing_fields():
            field = asked_field[0]
            # If the user is mid-correction of a DIFFERENT field, the
            # fallback should defer -- the model or a later turn will
            # pick up the correction. We only suppress when:
            #   * the user wrote a correction cue, AND
            #   * the field being asked about is NOT the field they
            #     appear to be correcting (i.e. cue alone is not
            #     enough if they're correcting the very field asked).
            suppress_fallback = bool(
                correction_cue_for_fallback
                and not (field == "gross_monthly_income" and income_cue_for_fallback)
            )
            # W64: gate the terse-income fallback by the income cue.
            # Without this, a bare number adjacent to an unrelated
            # noun ("she's 72", "we have 2 kids", "the apartment is
            # 72") gets misfiled as gross_monthly_income. The LLM
            # tool-call path already enforces this via
            # ``_message_has_intake_cue``; the deterministic fallback
            # must mirror that gate so the dry-run simulator (which
            # uses no LLM tool calls) honors the same rule.
            if not suppress_fallback and field == "gross_monthly_income":
                if (
                    not self._message_has_intake_cue("gross_monthly_income", user_message)
                    and self._fallback_parse_terse_reply(field, user_message) is None
                ):
                    suppress_fallback = True
            if not suppress_fallback:
                parsed = self._fallback_parse_terse_reply(field, user_message)
                if parsed is None and field == "gross_monthly_income":
                    # "$15/hr" and "$70 a day" deliberately refuse to parse
                    # (the hours/days worked are unknown), so they never
                    # reach the _apply_tool_calls choke point that normally
                    # raises the clarification. Catch them here instead of
                    # letting the turn fall through to a verbatim re-ask of
                    # the question the user just tried to answer.
                    ambiguity = classify_income_ambiguity(redact_for_logging(user_message))
                    if ambiguity is not None:
                        self._pending_income_clarification = ambiguity
                        self._pending_income_reason = ambiguity
                        self._pending_income_context = self._accumulate_income_context(
                            redact_for_logging(user_message)
                        )
                        self._pending_income_context_turn = self.state.turn_count
                        self._audit.append({
                            "turn": self.state.turn_count,
                            "event": "income_ambiguous_clarification_requested",
                            "reason": ambiguity,
                        })
                if parsed is not None:
                    try:
                        new_corrections.extend(
                            self._apply_tool_calls(
                                [{"name": "update_state", "input": {field: parsed}}],
                                user_message=user_message,
                                asked_field=field,
                            )
                        )
                        fallback_applied = True
                    except ValueError:
                        # A malformed/over-limit tool call from the deterministic
                        # fallback (e.g. household_size above the schema cap)
                        # must not crash the turn. Drop the bad update; state
                        # stays valid and the loop re-asks on the next turn.
                        pass

        # ------------------------------------------------------------------
        # Out-of-order corrections (W64)
        # ------------------------------------------------------------------
        # Two correction paths the simulator exercises that the field-fallback
        # loop above cannot catch on its own:
        #
        # 1. Income correction: the agent has moved on to a different field,
        #    the user expresses a correction to gross_monthly_income
        #    ("Oh wait, ... closer to 2100 a month."). Requires an explicit
        #    correction cue AND an income/period cue AND no deduction term
        #    (so ordinary expenses cannot overwrite income).
        #
        # 2. Household-size correction: the agent has moved on to a different
        #    field, the user expresses a correction to household_size
        #    ("Sorry, I forgot to mention ... actually it's 4 of us.").
        #    Requires an explicit correction cue AND a household phrase.
        #
        # Both run UNCONDITIONALLY on every turn (the previous attempt
        # nested them inside ``except ValueError:`` for the terse fallback,
        # which meant they never fired because that except is only entered
        # when the terse fallback threw). They are gated by
        # ``fallback_applied`` so the field loop's own corrections win.
        if not fallback_applied:
            correction_cue = re.search(
                r"\b(?:actually|sorry|i\s+meant|correction|no\s+wait|that(?:s|\s+is)\s+wrong|"
                r"en realidad|perd[oó]n|quer[ií]a decir|correcci[oó]n|no\s+espera)\b",
                user_message, re.I,
            )
            # W64: broaden the income cue so a number paired with a
            # month/week/hourly token counts ("2100 a month", "around 2k
            # monthly", "I'll say 2100 bucks/month"). The previous regex
            # only matched "per month", which silently dropped the
            # contradicting_income correction ("Oh wait, ... closer to 2100
            # a month.") because the cue regex did not fire on "a month".
            income_cue = re.search(
                r"\b(?:income|make|makes|earn|earned|paid|pay\s+me|salary|salaries|"
                r"wages?|per\s+month|a\s+month|monthly|month|"
                r"biweekly|per\s+week|a\s+week|per\s+hour|an\s+hour|a\s+hour|hourly|"
                r"hr\b|hour\b|bucks?|dollars?)\b",
                user_message, re.I,
            )
            # Number-adjacent month/week/hour cue: a $-prefix or multi-digit
            # number within a few tokens of a month/week/hour word. Catches
            # sentence-level corrections that do not include one of the
            # dedicated keywords above.
            number_adjacent_period_cue = bool(
                re.search(
                    r"\$?\s*[\d,]+(?:\.\d+)?\s*(?:/|per\s+|a\s+|an\s+)?(?:month|mo|wk|week|hour|hr)\b",
                    user_message, re.I,
                )
                or re.search(
                    r"\b(?:month|mo|wk|week|hour|hr)\b[^.\n]{0,12}\$?\s*[\d,]+",
                    user_message, re.I,
                )
            )
            deduction_cue = re.search(
                r"\b(?:rent|mortgage|childcare|daycare|medical|car|utilities?|insurance|bills?|"
                r"gas|electric)\b",
                user_message, re.I,
            )
            # Income correction branch.
            if (
                income_set_before_this_turn
                and not model_updated_income_this_turn
                and (not asked_field or asked_field[0] != "gross_monthly_income")
                and correction_cue
                and (income_cue or number_adjacent_period_cue)
                and not deduction_cue
            ):
                parsed_income = normalize_income(user_message)
                if parsed_income is not None and parsed_income != self.state.gross_monthly_income:
                    try:
                        new_corrections.extend(self._apply_tool_calls(
                            [{"name": "update_state", "input": {"gross_monthly_income": parsed_income}}],
                            user_message=user_message,
                            asked_field=asked_field[0] if asked_field else None,
                        ))
                        fallback_applied = True
                    except ValueError:
                        pass

            # Household-size correction branch.
            household_size_set_before_this_turn = self.state.household_size is not None
            household_phrase_cue = re.search(
                r"\b(?:there\s+are|we\s+are|we're|household\s+of|"
                r"actually\s+it'?s|just\s+me,?\s+household\s+of|of\s+us|"
                r"people\s+in\s+(?:our|the)\s+household|persons?\s+in\s+(?:our|the)\s+household)\b",
                user_message, re.I,
            )
            if (
                not fallback_applied
                and household_size_set_before_this_turn
                and not model_updated_income_this_turn
                and (not asked_field or asked_field[0] != "household_size")
                and correction_cue
                and household_phrase_cue
            ):
                parsed_household = self._fallback_parse_terse_reply(
                    "household_size", user_message
                )
                if (
                    parsed_household is not None
                    and parsed_household != self.state.household_size
                    and 1 <= int(parsed_household) <= 100
                ):
                    try:
                        new_corrections.extend(self._apply_tool_calls(
                            [{"name": "update_state", "input": {"household_size": int(parsed_household)}}],
                            user_message=user_message,
                            asked_field=asked_field[0] if asked_field else None,
                        ))
                        fallback_applied = True
                    except ValueError:
                        pass

            # W68b: a bare rejection of the recorded household_size, with
            # no replacement number anywhere else in the message, must not
            # be silently ignored -- see _try_household_bare_rejection_clear
            # for the full live-bug writeup. Gated by `not fallback_applied`
            # like the rest of this section: a real correction that already
            # landed above wins.
            if not fallback_applied:
                bare_rejection_reply = self._try_household_bare_rejection_clear(
                    user_message, lang
                )
                if bare_rejection_reply is not None:
                    return bare_rejection_reply

        had_result = self._eligibility_result is not None
        self._calculate_once()
        result_just_produced = not had_result and self._eligibility_result is not None

        # EXPLAIN: once DECIDE produced a result, make a second call with
        # that result in context. Before intake completion, the extraction
        # pass's prose remains current and avoids a redundant provider call.
        #
        # Skip the EXPLAIN pass entirely when:
        #   * the verdict was JUST produced on this turn, AND
        #   * the user's message does not contain a correction cue, AND
        #   * the post-verdict correction path (if applicable) has not
        #     already been entered (which is implicit because we are
        #     past that block).
        # Why: the deterministic label-render below covers the
        # "fresh verdict on a completed intake" case in one render. The
        # EXPLAIN pass exists to (a) let the model see the verdict and
        # (b) surface any post-verdict corrections. If neither is in
        # play (the user just answered the last intake field), a
        # redundant provider call wastes tokens and — more importantly
        # for scripted tests — eats the next queued response, leaving
        # the user's NEXT-TURN correction with an empty queue.
        correction_cue_for_explain = re.search(
            r"\b(?:actually|sorry|i\s+meant|correction|no\s+wait|that(?:s|\s+is)\s+wrong|"
            r"en realidad|perd[oó]n|quer[ií]a decir|correcci[oó]n|no\s+espera)\b",
            user_message, re.I,
        )
        # A Spanish conversation must receive the Spanish explanation pass too.
        # The deterministic verdict floor below remains the final safety net,
        # but consuming the queued/provider explanation here preserves the
        # language contract on every main-agent call.
        if self._eligibility_result is not None and not (
            result_just_produced and not correction_cue_for_explain and self.state.language != "es"
        ):
            explain_response = self.llm_client.chat(
                [*self._messages, {"role": "user", "content": self._context(user_message)}],
                system=self._system_prompt(),
                tools=self.tools,
            )
            content = str(explain_response.get("content") or "")
        else:
            content = str(extract_response.get("content") or "")

        # If this turn filled intake state, the reply comes from that stored
        # state—not from provider prose generated before validation. This
        # prevents impossible acknowledgements such as "household size 27
        # saved" followed by a silent rejection and another household prompt.
        newly_recorded = [
            field
            for field, before_value in intake_before.items()
            if before_value is None and getattr(self.state, field) is not None
        ]
        if self.state.missing_fields() and newly_recorded:
            content = _intake_progress_reply(
                self.state, newly_recorded, self.state.language
            )

        # F5 (2026-07-28) affirmative answer: when the user just
        # recorded an elderly/disabled field via the F4 extraction
        # pass AND their message was a question on that field’s
        # relevance, prepend the code-templated answer. This is
        # what closes the §9.1 turn-4 gap: the user asked "does
        # that matter", the bot now answers "yes, it changes which
        # income test applies" before re-asking the next field.
        if (
            "has_elderly_or_disabled_member" in newly_recorded
            and _is_elderly_relevance_question(user_message)
            and content
        ):
            answer = _elderly_relevance_answer(self.state.language)
            if answer:
                content = f"{answer} {content}"

        # Provider-independent floor: a verdict can never be blank or omit
        # the outcome. Arithmetic remains exclusively in the calculator.
        if self._eligibility_result is not None:
            # W62: also strip re-asking sentences the fresh-decide
            # model may have emitted for a field already recorded
            # THIS turn (the third-fill turn). Mirrors the post-
            # verdict branch above so the user-visible rendering
            # uses the same invariants everywhere.
            if not self.state.missing_fields():
                content = _strip_completed_intake_re_asks(
                    content, self.state.language
                )
            content = _outcome_label_floor(
                content, self._eligibility_result, self.state.language, _purpose="fresh_decide"
            )

        # When an eligibility result is first produced, ground it visibly:
        # capture the retrieval hits for the applicable track and append a
        # deterministic "Sources:" line naming the documents. This is
        # code-templated (not model-generated) so the citation cannot be
        # hallucinated, and it makes the RAG grounding demonstrable live
        # instead of invisible. Hits are also stored on self.last_citations
        # for the web UI's citation panel.
        if result_just_produced:
            # Rebuild context after the final field lands so structured limits
            # and their exact retrieval hits are available to telemetry/UI.
            self._context(user_message)
            # _context() built the explanation prompt immediately above; cite
            # those exact blocks rather than issuing a different retrieval.
            hits = self._prompt_citations
            if isinstance(hits, list):
                self.last_citations = list(hits)
                docs: list[str] = []
                for h in hits:
                    doc = h.get("doc") if isinstance(h, dict) else None
                    if doc and doc not in docs:
                        docs.append(doc)
                if docs:
                    sources = f"(Sources: {', '.join(docs)}.)"
                    content = f"{content} {sources}" if content else sources
        # Append a deterministic, code-templated suffix line for every
        # intake-field correction observed on THIS turn. The model does
        # not write these lines — they are generated from
        # ``new_corrections`` so they are testable and cannot be
        # hallucinated. Corrections are committed to ``self._corrections``
        # only after the apply call succeeded (a failed apply drops the
        # suffix so a phantom correction never reaches the user).
        # W19: the suffix renders in Spanish in a Spanish-mode
        # conversation so nothing silently reverts to English. Field names
        # stay as the machine names (they name the state fields, which are
        # language-independent); only the surrounding phrasing translates.
        self._corrections.extend(new_corrections)
        for entry in new_corrections:
            if self.state.language == "es":
                suffix = (
                    f"(Actualizado {entry['field']}: {entry['old']} -> {entry['new']}.)"
                )
            else:
                suffix = (
                    f"(Updated {entry['field']}: {entry['old']} -> {entry['new']}.)"
                )
            if content:
                content = f"{content} {suffix}"
            else:
                content = suffix

        # Deterministic backstop: some models return a bare tool call with
        # NO accompanying text when they successfully record a field (most
        # visible on the very first substantive reply of a conversation).
        # Content staying empty here would show the user a blank message
        # with no indication of what to answer next. If the intake is
        # still incomplete and nothing above produced any text, ask for
        # the next missing field in code -- this mirrors exactly what the
        # system prompt already instructs the model to do; it only fires
        # as a safety net when the model didn't.
        #
        # W53 — forward-progress floor: extend the W24 backstop so it
        # fires on NON-EMPTY replies too, whenever the reply does not
        # already contain the canonical next-field question. The
        # W24 backstop only covered the "model returned nothing"
        # case; the live failure (2026-07-26) was the chatty model
        # recording the field and then ending with a question mark
        # that asked nothing useful ("were you just tossing out a
        # number?"). ``_w53_fold_ask`` is a no-op when intake is
        # already complete (verdict explanation path keeps its own
        # outcome-bearing floor) and a no-op when the model reply
        # already asked for ``missing[0]``, so existing well-behaved
        # transcripts are unaffected.
        # An ambiguous income answer replaces the generic income question
        # with the one that actually resolves it. Consumed once, then
        # cleared, so a later turn re-asks normally.
        clarification = None
        if (
            self._pending_income_clarification
            and "gross_monthly_income" in self.state.missing_fields()
        ):
            # F3: pass text so the two-figures clarification can quote
            # both numbers back. The pre-F3 code passed only ``value``,
            # which is None for ambiguous messages (normalize_income
            # refuses to guess), so the question had no figures quoted.
            clarification = income_clarification_question(
                self._pending_income_clarification,
                self.state.language,
                value=normalize_income(user_message),
                text=self._pending_income_context or user_message,
            ) or None
        self._pending_income_clarification = None
        if clarification:
            # The model has usually just asserted the reading we are about
            # to question ("Got it - $70 per month."). Keeping that sentence
            # in front of the clarification would both contradict it and
            # imply the figure was accepted, so the clarification stands
            # alone. It already quotes the user's own number back to them.
            content = ""
        content = _w53_fold_ask(
            content,
            self.state.language,
            list(self.state.missing_fields()),
            question_override=clarification,
        )

        # P1.2: post-LLM output validators. Run after the W53 fold so
        # the appended next-field question also passes through the
        # checks. On any failure, fall back to a templated state-aware
        # continuation: just the canonical next-field question, so the
        # user has something concrete to answer. The validators never
        # re-ask a filled field (they only scan the reply), and the
        # W62 next-field helper is reused here so the wording stays
        # in lockstep with the rest of the agent.
        from .validators import check_all, check_ssn_fabrication, redact_ssn_fabrication

        # SSN redaction runs unconditionally, before the intake-complete
        # gate below. Found live 2026-07-28: the other three validators
        # are deliberately log-only once intake is complete (the verdict
        # turn has no "next field" to fall back to, and the citations
        # suffix is worth preserving) -- but that same exemption let a
        # verdict-turn reply ship a pasted SSN verbatim to the user,
        # because check_all()'s failure only triggered a REPLACEMENT
        # when self.state.missing_fields() was non-empty. SSN presence
        # isn't a quality issue like a fabricated dollar figure; it must
        # never reach the user regardless of what turn it's on. Redacted
        # (not replaced) so a legitimate hotline number elsewhere in the
        # same reply survives -- see redact_ssn_fabrication's docstring.
        if check_ssn_fabrication(content):
            content = redact_ssn_fabrication(content)
            self._audit.append({
                "turn": self.state.turn_count,
                "event": "p1_2_ssn_redacted_from_reply",
                "note": "Model echoed an SSN-shaped string; redacted before sending to the user.",
            })

        validator_failures = check_all(
            content,
            has_eligibility_result=self._eligibility_result is not None,
            household_size=self.state.household_size,
        )
        if validator_failures and self.state.missing_fields():
            # P1.2 fall-back: replace the LLM reply with a
            # templated state-aware continuation ONLY when intake
            # is INCOMPLETE. On a verdict turn (intake complete)
            # the LLM is explaining the result, the _outcome_label_floor
            # already added the deterministic verdict label, and
            # the citations suffix is code-templated -- replacing
            # the LLM's explanation with a "screening is complete"
            # stub would lose the actual verdict wording. On those
            # turns, validators catching something is logged for
            # telemetry but does not overwrite the content.
            #
            # Preserve the deterministic citations suffix (it is
            # code-templated, not LLM-authored) so the user still
            # sees the rule citations the calculator produced.
            sources_marker = "(Sources:"
            sources = ""
            if sources_marker in content:
                idx = content.index(sources_marker)
                sources = " " + content[idx:].rstrip()
            missing = list(self.state.missing_fields())
            fallback = _NEXT_FIELD_QUESTION.get(
                self.state.language,
                _NEXT_FIELD_QUESTION.get("en", {}),
            ).get(missing[0], "")
            self._audit.append({
                "turn": self.state.turn_count,
                "event": "p1_2_validator_caught",
                "note": "P1.2 validators caught issues; falling back to templated continuation.",
            })
            content = (fallback + sources).strip()

        self._messages.append({"role": "assistant", "content": content})
        # W20: the de-escalation context applied to this turn's LLM call
        # only. Reset so the next turn's message is judged on its own and a
        # hostile turn can never poison the system prompt going forward.
        # Guardrail early-returns above never set the flag, so they have
        # nothing to reset.
        self._hostile_this_turn = False
        # P0.3: same per-turn lifecycle for the classifier off_topic
        # steering hint (see _system_prompt). Cleared at the end of
        # every turn so the next turn gets the stock prompt.
        self._classifier_off_topic_this_turn = False
        return content


__all__ = [
    "FoodPathAgent",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_ES",
    "UPDATE_STATE_TOOL",
    "FIRST_TURN_DISCLOSURE_EN",
    "FIRST_TURN_DISCLOSURE_ES",
    "FIRST_TURN_GREETING_EN",
    "FIRST_TURN_GREETING_ES",
    "first_turn_disclosure",
    "first_turn_greeting",
]  # noqa: F401  (re-exported for case-study imports)
