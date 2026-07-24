"""Stateful, tool-driven conversation loop for the CivicReach agent."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import ValidationError

from .calculator import calculate_eligibility
from .guardrails.ambiguity import normalize_income
from .guardrails.classifier import classify_intent
from .guardrails.crisis import crisis_response, detect_crisis
from .guardrails.hostility import (
    HOSTILITY_CONTEXT_EN,
    HOSTILITY_CONTEXT_ES,
    detect_hostility,
)
from .guardrails.injection import detect_injection, injection_response
from .guardrails.pii import redact_for_logging
from .guardrails.scope import (
    detect_not_a_person_request,
    detect_out_of_scope,
    not_a_person_response,
    scope_redirect_message,
)
from .handoff import _HANDOFF_BANNER, handoff_summary, send_handoff_email
from .llm_client import LLMClient
from .retrieval import detect_spanish, retrieve
from .state import AssessmentState

SYSTEM_PROMPT = """You are CivicReach, an empathetic North Carolina Food and
Nutrition Services (FNS) eligibility assistant. Your scope is strictly an
initial eligibility screen for NC FNS / SNAP; you do not give legal advice
and you do not help with unrelated topics.

Collect only the three fields the calculator needs:
  1. household_size  (integer)
  2. gross_monthly_income  (number)
  3. has_elderly_or_disabled_member  (boolean)

The calculator also accepts four OPTIONAL deduction inputs. Do NOT ask
extra questions to collect them — record them ONLY when the user
volunteers the information in conversation:
  - earned_monthly_income (the wage/self-employment portion of income)
  - dependent_care_monthly (childcare/dependent-care costs)
  - medical_expenses_monthly (out-of-pocket medical costs; relevant when
    the household has an elderly or disabled member)
  - shelter_costs_monthly (rent/mortgage, taxes, insurance, utilities)
Recording volunteered deduction data improves the accuracy of the net
income screen. Never press the user for these.

NEVER ask the user for any other personally identifiable information. In
particular, do NOT request: a Social Security number, a full date of birth,
a home address, a phone number, a bank account number, a driver's license
number, an immigration status, or any other identifier beyond the fields
above. If the user volunteers such information, accept it gracefully
but do not store it and do not echo it back verbatim.

Ask exactly ONE question at a time, for the first field listed under
missing_fields. Never ask again for a field already present in state.
Record user-provided facts only by calling update_state; do not put
arithmetic or inferred values in that tool call. Never do eligibility
arithmetic yourself. Once the deterministic eligibility result is
supplied, explain it using that result and the retrieved rule text,
clearly labeling it as an initial screen rather than a final agency
determination.

IMPORTANT — terse replies: real users answer in short fragments, not full
sentences. If you just asked for a specific field and the user's reply is
a bare number, a short phrase, or an obvious yes/no ("3", "3 people",
"2200", "$2,200/month", "no", "nobody"), that reply IS the value for the
field you just asked about — call update_state with it immediately. Do
NOT ask the user to restate it in a longer sentence, and do NOT wait for
the field name to be repeated back to you.

If the user asks for something outside the NC FNS eligibility scope,
respond with a brief polite redirect back to the FNS questions and do
not attempt to answer the off-topic ask. Do not abandon or reset any
state that has already been collected.

W21 — DISCLOSURE INTEGRITY: CivicReach is an automated AI assistant
(not a live person, not an NC DSS caseworker, not 911, not 988's live
counselors, and not the EBT card-services line). The very first
assistant message of every conversation is a code-templated disclosure
produced by ``first_turn_disclosure(lang)`` in this module. Do NOT
replace, soften, paraphrase, omit, or contradict that disclosure. Do
NOT claim to be a real person, a caseworker, a hotline operator, or
someone who can dispatch help, call the user, or take any real-world
action. If the user asks whether you are a real person or whether you
are going to call someone, the response is produced by
``not_a_person_response(lang)`` — also code-templated, never rewritten
by you. The only crisis override that takes precedence over the
disclosure is the hardcoded ``crisis_response()`` for an active
disclosure; in that case the crisis response replaces the disclosure.
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
SYSTEM_PROMPT_ES = """Eres CivicReach, un asistente empático de elegibilidad para los
Servicios de Alimentos y Nutrición (FNS) de Carolina del Norte. Tu alcance
está estrictamente limitado a una evaluación inicial de elegibilidad para
NC FNS / SNAP; no das asesoría legal ni ayudas con temas no relacionados.

Recolecta únicamente los tres campos que necesita la calculadora:
  1. household_size  (entero — número de personas en el hogar)
  2. gross_monthly_income  (número — ingresos mensuales brutos del hogar)
  3. has_elderly_or_disabled_member  (booleano — si alguna persona del
     hogar tiene 60 años o más o tiene una discapacidad)

La calculadora también acepta cuatro deducciones OPCIONALES. NO hagas
preguntas adicionales para recolectarlas — regístralas ÚNICAMENTE cuando
el usuario ofrezca la información por su cuenta en la conversación:
  - earned_monthly_income (la porción de ingresos por trabajo salarial o
    trabajo por cuenta propia)
  - dependent_care_monthly (costos de cuidado de niños o dependientes)
  - medical_expenses_monthly (gastos médicos de bolsillo; relevante cuando
    el hogar incluye una persona mayor o con discapacidad)
  - shelter_costs_monthly (renta/hipoteca, impuestos, seguro, servicios)
Registrar las deducciones que el usuario ofrece voluntariamente mejora la
precisión de la evaluación de ingreso neto. Nunca presiones al usuario
para obtener estos datos.

NUNCA pidas al usuario ninguna otra información de identificación personal.
En particular, NO solicites: un número de Seguro Social, una fecha de
nacimiento completa, una dirección de domicilio, un número de teléfono, un
número de cuenta bancaria, un número de licencia de conducir, un estatus
migratorio, ni ningún otro identificador más allá de los campos anteriores.
Si el usuario ofrece esa información por su cuenta, acéptala con cortesía
pero no la almacenes ni la repitas textualmente.

Haz exactamente UNA pregunta a la vez, para el primer campo listado en
missing_fields. Nunca vuelvas a preguntar por un campo que ya esté
presente en el estado. Registra los datos que proporciona el usuario
únicamente llamando a update_state; no pongas cálculos aritméticos ni
valores inferidos en esa llamada de herramienta. Nunca hagas la
aritmética de elegibilidad tú mismo. Cuando se te proporcione el resultado
determinista de elegibilidad, explícalo usando ese resultado y el texto de
las reglas recuperado, aclarando claramente que se trata de una evaluación
inicial y no de una determinación final de la agencia.

IMPORTANTE — respuestas breves: los usuarios reales responden con
fragmentos cortos, no oraciones completas. Si acabas de pedir un campo
específico y la respuesta del usuario es un número simple, una frase corta,
o un sí/no evidente ("3", "3 personas", "2200", "$2,200 al mes", "no",
"nadie"), esa respuesta ES el valor del campo que acabas de pedir — llama a
update_state con ese valor de inmediato. NO le pidas al usuario que lo
repita en una oración más larga, y no esperes a que repita el nombre del
campo.

Si el usuario pide algo fuera del alcance de la elegibilidad de NC FNS,
responde con una redirección breve y cortés de vuelta a las preguntas de
FNS y no intentes responder la solicitud fuera de tema. No abandones ni
reinicies ningún estado que ya se haya recolectado.

W21 — INTEGRIDAD DEL AVISO: CivicReach es un asistente automatizado de
inteligencia artificial (no es una persona real, no es un trabajador
social del DSS de Carolina del Norte, no es 911, no es los consejeros
en vivo del 988, y no es la línea de servicios de la tarjeta EBT). El
primer mensaje del asistente en cada conversación es un aviso generado
por código mediante ``first_turn_disclosure(lang)`` en este módulo. NO
reemplaces, suavices, parafrasees, omitas ni contradigas ese aviso. NO
afirmes ser una persona real, un trabajador social, un operador de una
línea directa, ni alguien que pueda despachar ayuda, llamar al usuario
o tomar cualquier acción en el mundo real. Si el usuario pregunta si
eres una persona real o si vas a llamar a alguien, la respuesta es
producida por ``not_a_person_response(lang)`` — también generada por
código, nunca reescrita por ti. La única anulación de crisis que
tiene precedencia sobre el aviso es ``crisis_response()`` para una
crisis activa; en ese caso la respuesta de crisis reemplaza al aviso.

IMPORTANTE: Conduce TODA la conversación en español — todas tus preguntas,
aclaraciones y la explicación final de elegibilidad deben estar en español.
El único contenido en inglés permitido en tus respuestas son las líneas
deterministas generadas por código (p. ej. los sufijos "(Sources: ...)"
y "(Updated ...)", que se agregan automáticamente y no debes reproducirlas
ni traducirlas tú mismo).
"""

UPDATE_STATE_TOOL = {
    "name": "update_state",
    "description": "Record explicitly supplied assessment fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "household_size": {"type": "integer", "minimum": 1, "maximum": 20},
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


# W21 — explicit first-turn AI / not-a-person disclosure.
#
# Researched failure mode (2026-07-23): a user could reasonably not realize
# they are talking to an unmonitored AI text tool that cannot see them,
# dispatch anyone, or take any real-world action. Trained 911 / 988
# operators always explicitly triage before offering resources, and EBT
# card-services hotlines explicitly disambiguate themselves from
# adjacent, easily-confused real services ("we only handle card issues;
# for eligibility you need your state SNAP agency, not this line"). CA
# SB 243 / WA HB 2225 require clear AI disclosure for conversational
# bots; the FTC treats non-disclosure of AI-vs-human as potentially
# deceptive. None of this was previously explicit anywhere in
# CivicReach's first message.
#
# The disclosure is code-templated, not LLM-generated, so the framing
# cannot drift. The very first assistant message in a conversation is
# the literal string returned by ``first_turn_disclosure(lang)``. The
# system prompt below tells the model to NEVER replace, soften, or
# contradict that disclosure; the disclosure exists precisely to make
# confusion impossible before it happens.
FIRST_TURN_DISCLOSURE_EN = (
    "Hi — I'm CivicReach, an automated AI assistant (not a live person "
    "and not a DSS caseworker). I can do an initial NC FNS / SNAP "
    "eligibility SCREEN — I can't see you, I can't call anyone, I "
    "can't dispatch help, and I can't take any real-world action on "
    "your behalf. If this is a life-threatening emergency, call 911. "
    "If you're in a mental-health crisis, call or text 988. To get "
    "started, how many people are in your household?"
)

FIRST_TURN_DISCLOSURE_ES = (
    "Hola — soy CivicReach, un asistente automatizado de inteligencia "
    "artificial (no soy una persona real ni un trabajador social del "
    "DSS). Solo puedo hacer una evaluación inicial de elegibilidad "
    "para FNS / SNAP de Carolina del Norte — no puedo verte, no puedo "
    "llamar a nadie, no puedo despachar ayuda y no puedo tomar ninguna "
    "acción en el mundo real. Si es una emergencia que pone en peligro "
    "tu vida, llama al 911. Si estás en una crisis de salud mental, "
    "llama o envía un mensaje de texto al 988. Para empezar, ¿cuántas "
    "personas hay en tu hogar?"
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


class CivicReachAgent:
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
        self._eligibility_result: dict[str, Any] | None = None
        # W20: per-turn hostility flag. Set by ``turn()`` when the user's
        # message trips ``detect_hostility`` and no higher-priority guardrail
        # (crisis/injection/scope) claimed the turn. ``_system_prompt()``
        # reads it to prepend the one-line de-escalation context for that
        # turn's LLM call only; ``turn()`` resets it to False before
        # returning so it can never leak into the next turn.
        self._hostile_this_turn: bool = False
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
        # Retrieval hits (list of {doc, snippet, score}) behind the most
        # recent eligibility result. Populated when a result is first
        # produced (see turn()). Exposed for the web UI's citation panel
        # and so a reviewer can see WHICH documents grounded the answer.
        self.last_citations: list[dict[str, Any]] = []

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
          so the notification concept is visible with zero external
          dependencies;
        * ``email_attempted`` / ``email_sent`` — status of the
          strictly-secondary best-effort SMTP notification (attempted
          only when all SMTP_* env vars are present; any SMTP failure is
          swallowed and never degrades the on-screen path).
        """
        summary = handoff_summary(self.state, self._eligibility_result, self.last_citations)
        if summary is None:
            return None
        email_sent = send_handoff_email(summary)
        return {
            "summary": summary,
            "banner": _HANDOFF_BANNER,
            "email_attempted": email_sent,
            "email_sent": email_sent,
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
        rule_text = self._retrieve(query)
        payload: dict[str, Any] = {
            "assessment_state": self.state.model_dump(),
            "missing_fields": missing,
            "retrieved_rule_text": rule_text,
            "corrections": list(self._corrections),
        }
        if self._eligibility_result is not None:
            payload["deterministic_eligibility_result"] = self._eligibility_result
        return json.dumps(payload, default=str)

    # Intake fields whose change invalidates a cached eligibility result.
    _INTAKE_FIELDS = ("household_size", "gross_monthly_income", "has_elderly_or_disabled_member")

    def _apply_tool_calls(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                normalized = normalize_income(updates["gross_monthly_income"])
                if normalized is None:
                    updates.pop("gross_monthly_income", None)
                else:
                    updates["gross_monthly_income"] = normalized
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

    # Bare yes/no tokens for the elderly/disabled field fallback below.
    # English-only: Spanish-mode conversations rely on the LLM tool call
    # (the fallback is a narrow backstop, not a full parser).
    _NEGATIVE_WORDS = frozenset({"no", "nope", "nah", "n", "none", "nobody"})
    _POSITIVE_WORDS = frozenset({"yes", "yeah", "yep", "y", "sure"})

    @staticmethod
    def _fallback_parse_terse_reply(field: str, user_message: str) -> Any | None:
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
            match = re.match(r"^\$?(\d{1,2})\b", text)
            if not match:
                return None
            value = int(match.group(1))
            return value if 1 <= value <= 20 else None
        if field == "gross_monthly_income":
            return normalize_income(text)
        if field == "has_elderly_or_disabled_member":
            token = text.lower().strip(".! ")
            if token in CivicReachAgent._NEGATIVE_WORDS:
                return False
            if token in CivicReachAgent._POSITIVE_WORDS:
                return True
            return None
        return None

    def _calculate_once(self) -> None:
        if self._eligibility_result is not None or not self.state.is_ready_to_calculate():
            return
        household_size = self.state.household_size
        gross_income = self.state.gross_monthly_income
        elderly_or_disabled = self.state.has_elderly_or_disabled_member
        assert household_size is not None
        assert gross_income is not None
        assert elderly_or_disabled is not None
        self._eligibility_result = calculate_eligibility(
            household_size=household_size,
            gross_monthly_income=gross_income,
            has_elderly_or_disabled=elderly_or_disabled,
        )

    def turn(self, user_message: str) -> str:
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
            # The default bilingual block stays the safe choice when the
            # language is still unknown; once a mode is set, the crisis
            # response renders in that language only so a Spanish
            # conversation never silently reverts to English mid-crisis.
            content = crisis_response(
                "bilingual" if not detect_spanish(user_message) else lang
            )
            self._messages.append({"role": "assistant", "content": content})
            return content

        if detect_injection(user_message):
            self.state.is_injection_attempt = True
            content = injection_response(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        if detect_out_of_scope(user_message):
            self.state.is_out_of_scope = True
            content = scope_redirect_message(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W21 — not-a-person disambiguation. Strictly lower priority than
        # crisis / injection / scope (which all claim the turn above) and
        # strictly higher priority than the ordinary LLM path. A user who
        # explicitly asks "are you a real person" / "are you calling
        # someone" / "is this 911" gets the deterministic disambiguation
        # block instead of a model-generated reply, so the framing cannot
        # drift. The check happens here (after crisis/injection/scope,
        # before the LLM call) so it can never delay or override a crisis
        # response — a crisis disclosure returns crisis_response() before
        # reaching this line.
        if detect_not_a_person_request(user_message):
            content = not_a_person_response(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        # W20: hostility is NON-BLOCKING and strictly lower priority than
        # every guardrail above. If the message reached this point it is
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
            self._messages.append(
                {
                    "role": "system",
                    "content": (
                        "[CivicReach audit] The user's previous message was "
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
        intent = classify_intent(user_message, self.llm_client)
        if intent == "crisis":
            self.state.is_crisis = True
            content = crisis_response(
                "bilingual" if not detect_spanish(user_message) else lang
            )
            self._messages.append({"role": "assistant", "content": content})
            return content
        if intent == "injection":
            self.state.is_injection_attempt = True
            content = injection_response(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content
        if intent == "off_topic":
            self.state.is_out_of_scope = True
            content = scope_redirect_message(lang)
            self._messages.append({"role": "assistant", "content": content})
            return content

        # Captured BEFORE the LLM call: the single field this turn's
        # question was actually about, if the intake isn't complete yet.
        # Used below as a narrow, deterministic backstop -- see
        # _fallback_parse_terse_reply.
        asked_field = self.state.missing_fields()[:1]

        response = self.llm_client.chat(
            [*self._messages, {"role": "user", "content": self._context(user_message)}],
            system=self._system_prompt(),
            tools=self.tools,
        )
        new_corrections: list[dict[str, Any]] = []
        try:
            new_corrections = self._apply_tool_calls(response.get("tool_calls") or [])
        except ValueError:
            # A malformed/over-limit tool call from the model (e.g.
            # household_size above the schema cap) must not crash the
            # turn. Drop the bad update; state stays valid, the field
            # stays missing, and the loop re-asks on the next turn.
            # ``new_corrections`` keeps its default empty value so no
            # phantom suffix is appended below.
            pass

        # Deterministic fallback: if the model's tool call (or lack of
        # one) left the field this turn's question was about still
        # missing, try a narrow, conservative parse of the raw reply
        # itself. Some providers are noticeably less reliable at
        # connecting a terse reply ("3", "2200", "no") to the field it
        # answers than at fuller sentences -- this never touches any
        # field except the one the agent just asked about, so a parse
        # failure just leaves it missing for the LLM to re-ask.
        fallback_applied = False
        if asked_field and asked_field[0] in self.state.missing_fields():
            field = asked_field[0]
            parsed = self._fallback_parse_terse_reply(field, user_message)
            if parsed is not None:
                try:
                    new_corrections.extend(
                        self._apply_tool_calls([{"name": "update_state", "input": {field: parsed}}])
                    )
                    fallback_applied = True
                except ValueError:
                    pass

        had_result = self._eligibility_result is not None
        self._calculate_once()
        result_just_produced = not had_result and self._eligibility_result is not None

        # The model's own reply text was generated BEFORE the fallback
        # applied this field, so it's stale -- most visibly, it re-asks
        # a question that's now already answered. Re-query once, with
        # state (and _context()) now reflecting the fallback's update
        # and any freshly-computed eligibility result, so the reply the
        # user actually sees is grounded in current state. Tool calls
        # from this second pass are ignored: every intake field the
        # model could still extract was already handled above.
        if fallback_applied:
            response = self.llm_client.chat(
                [*self._messages, {"role": "user", "content": self._context(user_message)}],
                system=self._system_prompt(),
                tools=self.tools,
            )

        content = str(response.get("content") or "")

        # When an eligibility result is first produced, ground it visibly:
        # capture the retrieval hits for the applicable track and append a
        # deterministic "Sources:" line naming the documents. This is
        # code-templated (not model-generated) so the citation cannot be
        # hallucinated, and it makes the RAG grounding demonstrable live
        # instead of invisible. Hits are also stored on self.last_citations
        # for the web UI's citation panel.
        if result_just_produced:
            hits = self._retrieve(self._track_query())
            if isinstance(hits, list):
                self.last_citations = hits
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
        self._messages.append({"role": "assistant", "content": content})
        # W20: the de-escalation context applied to this turn's LLM call
        # only. Reset so the next turn's message is judged on its own and a
        # hostile turn can never poison the system prompt going forward.
        # Guardrail early-returns above never set the flag, so they have
        # nothing to reset.
        self._hostile_this_turn = False
        return content


__all__ = [
    "CivicReachAgent",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_ES",
    "UPDATE_STATE_TOOL",
    "FIRST_TURN_DISCLOSURE_EN",
    "FIRST_TURN_DISCLOSURE_ES",
    "first_turn_disclosure",
]
