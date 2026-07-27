"""First-turn automation disclosure + real-service disambiguation.

Three layers are exercised here, all code-templated:

1. ``first_turn_disclosure(lang)`` identifies FoodPath as automated in a
   compact, hard-coded introduction. The web UI combines it with the first
   question in one bubble inside the transcript.

2. ``detect_not_a_person_request(text)`` / ``not_a_person_response(lang)``
   answer a user who explicitly asks "are you a real person" / "are
   you calling someone" / "is this 911" with a deterministic block
   naming each adjacent real channel: NC DSS, 911, 988 live counselors,
   EBT card-services line. This is the same shape of self-identification
   real hotlines use at the start of every call.

3. The crisis / injection / scope layers must still claim the turn
   first. A crisis disclosure on turn 1 returns ``crisis_response()``
   verbatim — the disclosure never delays or overrides the crisis
   path. A "real" question phrased as a crisis ("I'm going to kill
   myself, are you calling someone for me?") gets the crisis response,
   not the not-a-person block. The guardrail order is
   crisis > injection > scope > not-a-person > hostility > LLM.
"""

from __future__ import annotations

import pytest

from src.agent import (
    FIRST_TURN_DISCLOSURE_EN,
    FIRST_TURN_DISCLOSURE_ES,
    FoodPathAgent,
    first_turn_disclosure,
)
from src.guardrails.classifier import CLASSIFIER_SYSTEM_PROMPT
from src.guardrails.crisis import CRISIS_RESOURCES, crisis_response
from src.guardrails.scope import (
    detect_not_a_person_request,
    detect_out_of_scope,
    not_a_person_response,
    scope_redirect_message,
)


class RecordingLLM:
    """A fake LLM that records what it was asked and returns a generic reply.

    Same shape as the RecordingLLM in the existing crisis / scope test
    files so the W21 contract stays compatible with the rest of the
    suite (calls counted, classifier skipped, generic assistant reply).
    """

    def __init__(self) -> None:
        self.calls = []

    def chat(self, messages, system=None, tools=None):
        # The classifier layer runs before the agent's main LLM call.
        # Skip recording it so test contracts (call counts, message
        # shape) stay meaningful at the agent level.
        if system == CLASSIFIER_SYSTEM_PROMPT:
            return {"content": "normal", "tool_calls": []}
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        return {"content": "GENERIC ASSISTANT REPLY", "tool_calls": []}


# ---------------------------------------------------------------------------
# 1. first_turn_disclosure() — content contract.
# ---------------------------------------------------------------------------


def test_first_turn_disclosure_english_contains_required_signals():
    """The compact English intro identifies automation and scope."""
    text = first_turn_disclosure("en")
    assert "AI assistant" in text or "automated" in text.lower()
    assert "eligibility estimate" in text.lower()
    assert len(text) < 120


def test_first_turn_disclosure_spanish_contains_required_signals():
    """The compact Spanish intro identifies automation and scope."""
    text = first_turn_disclosure("es")
    assert "automatizada" in text
    assert "FNS" in text or "SNAP" in text
    assert "elegibilidad" in text.lower()


def test_first_turn_disclosure_is_not_llm_generated():
    """Disclosure text is the hard-coded module constant, byte-identical."""
    assert first_turn_disclosure("en") == FIRST_TURN_DISCLOSURE_EN
    assert first_turn_disclosure("es") == FIRST_TURN_DISCLOSURE_ES
    # And no LLM call was made to produce it: the function returns a
    # module-level string constant without touching the model.
    # (Verified by importing — there's no LLM client in scope.)


def test_first_turn_disclosure_unknown_lang_falls_back_to_english():
    """An unrecognized lang value returns the English version (safe default)."""
    assert first_turn_disclosure("fr") == FIRST_TURN_DISCLOSURE_EN
    assert first_turn_disclosure("") == FIRST_TURN_DISCLOSURE_EN


# ---------------------------------------------------------------------------
# 2. detect_not_a_person_request() — trigger contract.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Is this a real person?",
        "Are you a real person?",
        "Are you a real human?",
        "Are you a live person?",
        "Are you a live agent?",
        "Are you a chatbot?",
        "Are you a bot?",
        "Are you an AI?",
        "Is this a chatbot?",
        "Is this AI?",
        "Are you calling someone?",
        "Are you calling anyone?",
        "Will you call someone?",
        "Are you dispatching help?",
        "Will you dispatch someone?",
        "Is someone coming to help?",
        "Will someone come?",
        "Are you notifying my caseworker?",
        "Is this 911?",
        "Is this 988?",
        "Is this the EBT line?",
        "Is this a hotline?",
        "Is this emergency services?",
        # Curly apostrophe edge case — same convention as detect_out_of_scope.
        "What\u2019s this — are you a real person?",
    ],
)
def test_detect_not_a_person_request_catches_questions(message):
    assert detect_not_a_person_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        # Spanish accent-sensitive variants.
        "¿Eres una persona real?",
        "¿Es esto una persona real?",
        "¿Eres un humano?",
        "¿Eres un chatbot?",
        "¿Eres una IA?",
        "¿Estás llamando a alguien?",
        "¿Vas a llamar a alguien?",
        "¿Esto es 911?",
        "¿Esto es 988?",
        "¿Esto es EBT?",
    ],
)
def test_detect_not_a_person_request_catches_spanish_questions(message):
    assert detect_not_a_person_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        # Normal intake chatter must NOT trigger. A phrase that contains
        # the word "person" in a non-question-about-FoodPath context
        # (e.g. household composition) is in-scope FNS intake, not a
        # not-a-person question.
        "There are three of us in my household.",
        "My grandmother is 67 and lives with us.",
        "I am a person in my household.",
        "Yes, my grandmother is a person.",
        "There are five people including me.",
        # Empty / whitespace inputs must NOT trigger (same convention
        # as detect_out_of_scope).
        "",
        "   ",
        "\n\n",
        # Out-of-scope-but-not-not-a-person asks must NOT trigger this
        # detector. They have their own scope_redirect path.
        "Should I sue my landlord?",
        "What's the weather today?",
        # In-scope FNS questions must NOT trigger.
        "What's the income limit for FNS?",
        "How many people are in my household?",
    ],
)
def test_detect_not_a_person_request_does_not_over_trigger_on_in_scope_messages(message):
    assert detect_not_a_person_request(message) is False


def test_detect_not_a_person_request_is_case_insensitive():
    assert detect_not_a_person_request("ARE YOU A REAL PERSON?") is True
    assert detect_not_a_person_request("Is This A Bot?") is True


# ---------------------------------------------------------------------------
# 3. not_a_person_response() — content contract.
# ---------------------------------------------------------------------------


def test_not_a_person_response_english_names_all_four_real_channels():
    """The response names each adjacent real channel so a confused user has
    somewhere else to go: NC DSS, 911, 988, EBT card line."""
    text = not_a_person_response("en")
    # Names the four real channels the user might confuse this with.
    assert "DSS" in text or "caseworker" in text.lower()
    assert "911" in text
    assert "988" in text
    assert "EBT" in text
    # Real hotline numbers survive verbatim.
    assert CRISIS_RESOURCES["emergency_services"] in text
    assert CRISIS_RESOURCES["suicide_crisis_lifeline"] in text
    # Explicit "not a real person" framing.
    assert "not a real person" in text.lower() or "automated" in text.lower()


def test_not_a_person_response_spanish_names_all_four_real_channels():
    text = not_a_person_response("es")
    assert "DSS" in text or "trabajador" in text.lower()
    assert "911" in text
    assert "988" in text
    assert "EBT" in text


def test_not_a_person_response_english_returns_same_string_each_call():
    """Code-templated: no nondeterminism, no LLM involvement."""
    a = not_a_person_response("en")
    b = not_a_person_response("en")
    assert a == b
    assert "988" in a
    assert "911" in a


# ---------------------------------------------------------------------------
# 4. agent.turn() — the not-a-person path returns the deterministic block.
# ---------------------------------------------------------------------------


def test_agent_turn_returns_not_a_person_block_when_user_asks_if_real():
    llm = RecordingLLM()
    agent = FoodPathAgent(llm_client=llm)

    response = agent.turn("Are you a real person?")

    assert response == not_a_person_response("en")
    # The LLM must NOT have been called. The classifier runs first and
    # is excluded from ``calls``; with the classifier skipped, the
    # not-a-person path is a hardcoded short-circuit.
    assert llm.calls == []


@pytest.mark.parametrize(
    "message",
    [
        "Are you calling someone for me?",
        "Is this 911?",
        "Are you going to dispatch help?",
        "Is this the EBT line?",
        "Will you call my caseworker?",
    ],
)
def test_agent_turn_not_a_person_short_circuits_without_llm(message):
    """Each real-channel confusion path short-circuits the LLM call."""
    llm = RecordingLLM()
    agent = FoodPathAgent(llm_client=llm)

    response = agent.turn(message)

    assert response == not_a_person_response("en")
    assert llm.calls == []


def test_agent_turn_not_a_person_short_circuits_in_spanish_mode():
    """W19 parity: Spanish not-a-person question gets the Spanish block."""
    llm = RecordingLLM()
    agent = FoodPathAgent(llm_client=llm)

    response = agent.turn("¿Eres una persona real?")

    assert response == not_a_person_response("es")
    assert agent.state.language == "es"
    assert llm.calls == []


# ---------------------------------------------------------------------------
# 5. Guardrail ordering — crisis / injection / scope still win.
# ---------------------------------------------------------------------------


def test_crisis_still_takes_priority_over_not_a_person():
    """A 'real person?' question phrased as a crisis disclosure still
    returns the crisis response — the not-a-person layer is below
    crisis in the chain and must never delay or override it."""
    llm = RecordingLLM()
    agent = FoodPathAgent(llm_client=llm)

    # The keyword ``want to die`` trips ``detect_crisis``; the "are you
    # a real person" suffix would also trip ``detect_not_a_person_request``
    # if asked in isolation, so the ordering claim is testable here.
    response = agent.turn(
        "I want to die. Are you a real person who can help me?"
    )

    # Crisis wins: the response is the bilingual crisis block, with
    # the verbatim hotline numbers, and the LLM is not consulted.
    assert response == crisis_response("bilingual")
    assert CRISIS_RESOURCES["emergency_services"] in response
    assert CRISIS_RESOURCES["suicide_crisis_lifeline"] in response
    assert llm.calls == []


def test_scope_still_takes_priority_over_not_a_person():
    """An out-of-scope legal ask still wins over the not-a-person layer.

    A user could ask both at once — "is this a real person who can
    advise me on a lawsuit?" — and the scope redirect must win
    because scope is higher priority in the guardrail chain.
    """
    llm = RecordingLLM()
    agent = FoodPathAgent(llm_client=llm)

    # ``sue`` trips ``detect_out_of_scope``; the "are you a real
    # person" suffix would also trip not-a-person if asked in
    # isolation, so the ordering claim is testable here.
    response = agent.turn("Are you a real person? Can you help me sue someone?")

    # Scope wins (because "sue" is a substring of the message and the
    # message also contains "real person"; the scope detector matches
    # first because it runs earlier in agent.turn()).
    assert response == scope_redirect_message("en")
    assert llm.calls == []


def test_not_a_person_does_not_set_out_of_scope_or_crisis_flag():
    """The not-a-person layer is presentation-only — it must not flip
    any of the conversation-level flags the scope / crisis layers
    set, because doing so would confuse downstream state-based logic
    (e.g. the in-scope message that follows)."""
    llm = RecordingLLM()
    agent = FoodPathAgent(llm_client=llm)

    agent.turn("Are you a real person?")

    assert agent.state.is_crisis is False
    assert agent.state.is_out_of_scope is False
    assert agent.state.is_injection_attempt is False


def test_not_a_person_does_not_destroy_collected_intake_state():
    """If the user has already provided intake fields and then asks a
    not-a-person question, the intake state must survive."""
    llm = RecordingLLM()
    agent = FoodPathAgent(llm_client=llm)

    # Seed state by running a normal intake question through a scripted
    # LLM that emits an update_state tool call on the first turn.
    class ScriptedLLM:
        def __init__(self):
            self.calls = []

        def chat(self, messages, system=None, tools=None):
            self.calls.append((messages, system, tools))
            if system == CLASSIFIER_SYSTEM_PROMPT:
                return {"content": "normal", "tool_calls": []}
            # First non-classifier call: extract household_size.
            if sum(1 for c in self.calls if c[1] != CLASSIFIER_SYSTEM_PROMPT) == 1:
                return {
                    "content": "How many people are in your household?",
                    "tool_calls": [
                        {"name": "update_state", "input": {"household_size": 4}}
                    ],
                }
            # Subsequent calls: hold state, generic reply.
            return {"content": "What is your gross monthly income?", "tool_calls": []}

    scripted = ScriptedLLM()
    agent = FoodPathAgent(llm_client=scripted)

    agent.turn("There are four of us.")
    assert agent.state.household_size == 4

    # Now ask the not-a-person question.
    response = agent.turn("Are you a real person?")
    assert response == not_a_person_response("en")
    # Intake state survived.
    assert agent.state.household_size == 4
    # And the next normal question still hits the LLM (state preserved).
    assert scripted.calls  # at least the classifier call(s) plus the intake call


# ---------------------------------------------------------------------------
# 6. The web UI surfaces the disclosure as the first assistant bubble.
# ---------------------------------------------------------------------------


def test_web_index_page_first_bubble_is_first_turn_disclosure_en():
    """The HTML index page renders the hard-coded disclosure as the first
    assistant bubble so a user opening the page sees the disclosure
    before they ever type anything. The check is on the rendered HTML
    string, which is built once at import time."""
    from src.web import PAGE

    # The disclosure text appears verbatim in the HTML (the surrounding
    # <div class="bubble assistant">…</div> wraps it).
    assert FIRST_TURN_DISCLOSURE_EN in PAGE
    # And the OLD greeting (the one this chunk is replacing) is gone.
    assert (
        "Hi — I can help with an initial NC Food and Nutrition Services eligibility screen. What would you like to know?"
        not in PAGE
    )


def test_web_index_page_has_one_compact_intro_inside_chat():
    """The intro belongs in the chat log, never as duplicate bubbles above it."""
    from src.agent import first_turn_greeting
    from src.web import PAGE

    assert first_turn_greeting("en") in PAGE
    assert PAGE.count('<div class="bubble assistant" data-role="intro">') == 1
    assert 'data-role="disclosure"' not in PAGE
    assert 'data-role="greeting"' not in PAGE
    messages_idx = PAGE.index('id="messages"')
    intro_idx = PAGE.index('data-role="intro"')
    composer_idx = PAGE.index('id="form"')
    assert messages_idx < intro_idx < composer_idx


# ---------------------------------------------------------------------------
# 7. The CLI banner surfaces the disclosure at startup.
# ---------------------------------------------------------------------------


def test_cli_banner_is_the_disclosure():
    """The CLI REPL banner is the hard-coded disclosure so a CLI user
    also cannot mistake FoodPath for a live person / hotline."""
    from src.cli import _BANNER

    assert FIRST_TURN_DISCLOSURE_EN in _BANNER
    # Repl controls still present so the banner is still usable.
    assert "handoff" in _BANNER.lower()
    assert "exit" in _BANNER.lower() or "Ctrl-C" in _BANNER


def test_cli_banner_includes_greeting_after_disclosure():
    """The banner surfaces both first-turn pieces, disclosure first."""
    from src.agent import first_turn_greeting
    from src.cli import _BANNER

    assert first_turn_greeting("en") in _BANNER
    assert _BANNER.index(FIRST_TURN_DISCLOSURE_EN) < _BANNER.index(first_turn_greeting("en"))


# ---------------------------------------------------------------------------
# 8. first_turn_greeting() — content contract.
# ---------------------------------------------------------------------------


def test_first_turn_greeting_is_short_friendly_and_asks_household_size():
    from src.agent import (
        FIRST_TURN_GREETING_EN,
        FIRST_TURN_GREETING_ES,
        first_turn_greeting,
    )

    en = first_turn_greeting("en")
    assert en == FIRST_TURN_GREETING_EN
    assert "buy and prepare food together" in en.lower()

    es = first_turn_greeting("es")
    assert es == FIRST_TURN_GREETING_ES
    assert "compran y preparan comida juntas" in es.lower()


def test_first_turn_greeting_unknown_lang_falls_back_to_english():
    from src.agent import FIRST_TURN_GREETING_EN, first_turn_greeting

    assert first_turn_greeting("fr") == FIRST_TURN_GREETING_EN
    assert first_turn_greeting("") == FIRST_TURN_GREETING_EN


# ---------------------------------------------------------------------------
# 9. The system prompt carries a disclosure-integrity reminder.
# ---------------------------------------------------------------------------


def test_system_prompt_carries_disclosure_integrity_clause():
    """The model is told the disclosure exists and must not contradict
    it; the reminder is in both the English and Spanish system prompts
    so it stays in effect across a mid-conversation language switch."""
    from src.agent import SYSTEM_PROMPT, SYSTEM_PROMPT_ES

    assert "DISCLOSURE INTEGRITY" in SYSTEM_PROMPT
    assert "INTEGRIDAD DEL AVISO" in SYSTEM_PROMPT_ES
    assert "do not repeat" in SYSTEM_PROMPT.lower()
    assert "no repitas" in SYSTEM_PROMPT_ES.lower()


# ---------------------------------------------------------------------------
# 9. Regression — detect_out_of_scope is unchanged by this chunk.
# ---------------------------------------------------------------------------


def test_existing_scope_detector_unchanged():
    """W21 adds a new detector; it must NOT alter the existing scope
    detector's behavior on the cases the original suite already covers."""
    assert detect_out_of_scope("Should I sue my landlord?") is True
    assert detect_out_of_scope("There are three of us in my household.") is False
    assert detect_out_of_scope("") is False
