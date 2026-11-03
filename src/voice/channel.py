"""The voice (IVR) channel for FoodPath.

This module adapts the existing :class:`~src.agent.FoodPathAgent` to a
spoken channel. It does not reimplement the agent. Every intake
behavior, every eligibility calculation, and every citation rule stays
where it already is, and this module only changes how a turn arrives
and how the reply is spoken.

That separation is the whole design. A second implementation of the
intake logic would be a second thing to keep correct, and the safety
argument for FoodPath rests on there being exactly one place where a
fact is recorded.

**Guardrail parity.** The voice channel cannot bypass a guardrail the
text channel has. It calls the same detector functions the agent calls,
in the same order, and it uses the same hardcoded responses -- except
for the crisis response, which has a spoken rendering because the text
rendering's instruction is wrong on a phone line (see
:mod:`src.voice.crisis_voice`).

**Silence is not an empty message.** On a text interface, an empty
message means the user pressed enter. On a voice line, silence means
the caller stopped talking, which is a different event with a different
cause and a different remedy: the recognizer may have cut them off, the
line may have dropped, the caller may have put the phone down, or they
may have fallen asleep. Treating silence as an empty message would
send an empty string into the agent and produce a confusing reply that
tells the caller to type something. :meth:`VoiceChannel.handle_silence`
handles it as its own case, re-prompts a bounded number of times, and
then offers a human.

**DTMF is an accessibility path.** A voice-only benefits line excludes
callers who cannot speak: a caller with a speech disability, a caller
on a line that drops audio, a caller in a place where speaking aloud is
unsafe, a caller who simply prefers not to talk to a machine. None of
those callers should be locked out of a food-assistance screen because
the only input method is speech. So the channel implements a real,
if small, keypad path: digits select a field, star repeats the last
prompt, pound confirms, and zero reaches a person. It is deliberately
minimal rather than pretending to be a full telephony IVR, because a
half-built keypad path that silently fails is worse than a small one
that works.

**Spoken register.** Every reply is normalized for speech: no markdown,
no bullet characters, no parentheses, no URLs, no ampersands. A reply
that reads fine on screen can be unintelligible read aloud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..agent import FoodPathAgent, first_turn_disclosure, first_turn_greeting
from ..guardrails.crisis import detect_crisis
from ..guardrails.injection import detect_injection, injection_response
from ..guardrails.scope import (
    detect_not_a_person_request,
    not_a_person_response,
    scope_redirect_message,
)
from .confirmation import (
    ConfirmationOutcome,
    classify_confirmation,
    should_escalate,
    should_reprompt,
)
from .crisis_voice import voice_crisis_response


# How many consecutive silent turns before the call is offered to a
# person. Silence is bounded because an unbounded silence loop is a
# caller listening to a machine say "I didn't hear anything" forever.
MAX_SILENT_TURNS = 3

# The reply given when the caller could not be understood.
UNHEARD_REPLY = (
    "I did not hear anything. Please speak again, or press 0 to talk to "
    "a person."
)

# The reply given when the confirmation limit is reached.
HANDOFF_REPLY = (
    "I am having trouble understanding your answers, so I am going to "
    "connect you with a person who can help you finish. Please hold."
)

# The reply given when the caller asks for a person.
REQUESTED_HANDOFF_REPLY = (
    "I am connecting you with a person now. Please hold."
)

# DTMF digit used to reach a person.
DTMF_HUMAN = "0"

# DTMF digit used to repeat the last prompt.
DTMF_REPEAT = "*"

# DTMF digit used to confirm the current field.
DTMF_CONFIRM = "#"

# The fields a caller can select with a DTMF digit, in order. Digit 1
# is the first field, digit 2 the second, and so on.
DTMF_FIELD_ORDER = (
    "household_size",
    "gross_monthly_income",
    "has_elderly_or_disabled_member",
)

# The plain-language name of each field, used when a caller selects one
# by digit. These mirror the questions the agent asks, shortened for
# speech.
DTMF_FIELD_LABELS = {
    "household_size": "how many people in your household",
    "gross_monthly_income": "your household's monthly income before taxes",
    "has_elderly_or_disabled_member": (
        "whether anyone in your household is 60 or older, or disabled"
    ),
}


@dataclass
class VoiceTurn:
    """The result of processing one turn on the voice channel."""

    # What the agent says back, normalized for speech.
    reply: str = ""
    # Whether the call has ended.
    ended: bool = False
    # Whether the call was handed to a person.
    escalated: bool = False
    # The confirmation outcome for this turn, if the turn was a
    # confirmation.
    outcome: ConfirmationOutcome | None = None
    # Notes for the audit log.
    notes: tuple[str, ...] = ()
    # Which guardrail or path handled the turn.
    handled_by: str = "agent"
    # The state of the assessment after the turn.
    state: dict[str, Any] = field(default_factory=dict)


def to_spoken(text: str) -> str:
    """Normalize a reply for delivery by a speech engine.

    Removes the conventions of a chat bubble that make no sense read
    aloud, and collapses whitespace so the TTS engine does not pause at
    the line breaks.
    """
    if not text:
        return ""
    cleaned = text.replace("&", " and ")
    # Bullet markers at the start of a line.
    cleaned = re.sub(r"(?m)^\s*[-*•]\s*", "", cleaned)
    # Inline emphasis markers.
    cleaned = cleaned.replace("**", "").replace("__", "")
    # Parentheses are noise read aloud.
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    # A URL is meaningless over the phone.
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = cleaned.replace("`", "")
    # Collapse whitespace, including the newlines.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class VoiceChannel:
    """Adapts a :class:`FoodPathAgent` to a spoken channel.

    Parameters
    ----------
    agent:
        The agent doing the actual work. Required: the voice channel
        never creates its own agent, because an agent with different
        configuration is an agent with different behavior.
    language:
        ``'en'`` or ``'es'``, matching the agent's language mode.
    """

    def __init__(self, agent: FoodPathAgent, *, language: str = "en") -> None:
        self.agent = agent
        self.language = language
        self.turn_count = 0
        self.silent_turns = 0
        self.confirmation_attempts = 0
        self.last_prompt = ""
        self.ended = False

    # -- lifecycle --------------------------------------------------------

    def greeting(self) -> str:
        """Return the spoken first-turn disclosure and opening question.

        The content mirrors the text channel's disclosure: it names the
        product, says it is automated, says what it cannot do, and points
        at 911 and 988. It is rendered in spoken register, and it says
        to hang up rather than call, because the caller is on a phone.
        """
        disclosure = first_turn_disclosure(self.language)
        # The text disclosure says "call 911" and "call or text 988".
        # On a voice line the caller is already on a call.
        spoken_disclosure = to_spoken(disclosure).replace(
            "call 911", "hang up and call 911"
        )
        greeting = first_turn_greeting(self.language)
        self.last_prompt = to_spoken(greeting)
        return to_spoken(f"{spoken_disclosure} {greeting}")

    # -- the main turn ----------------------------------------------------

    def handle_turn(self, spoken_text: str) -> VoiceTurn:
        """Process one spoken turn.

        The guardrail order matches the text channel: crisis first,
        because a message can match both the crisis detector and the
        injection detector and answering a crisis with an injection
        refusal is the worst outcome this system can produce. Injection
        second, because a caller steering the agent must be refused
        before the agent reasons about what they asked for.
        """
        if self.ended:
            return VoiceTurn(
                reply="",
                ended=True,
                notes=("call_already_ended",),
                handled_by="terminated",
            )

        text = (spoken_text or "").strip()
        if not text:
            return self.handle_silence()

        # A caller who spoke resets the silence counter.
        self.silent_turns = 0

        # 1. Crisis. Handled by the voice rendering, not the text one.
        if detect_crisis(text):
            self.ended = True
            return VoiceTurn(
                reply=to_spoken(voice_crisis_response(self.language)),
                ended=True,
                notes=("crisis",),
                handled_by="crisis",
                state=self._state(),
            )

        # 2. Prompt injection. Same detector, same hardcoded response.
        if detect_injection(text):
            return VoiceTurn(
                reply=to_spoken(injection_response(self.language)),
                notes=("injection",),
                handled_by="injection",
                state=self._state(),
            )

        # 3. The not-a-person question. A caller who asks whether they
        #    are talking to a person needs the same answer the text
        #    channel gives.
        if detect_not_a_person_request(text):
            return VoiceTurn(
                reply=to_spoken(not_a_person_response(self.language)),
                notes=("not_a_person",),
                handled_by="not_a_person",
                state=self._state(),
            )

        # 4. Ordinary intake. The agent does the work.
        self.turn_count += 1
        reply = self.agent.turn(text)
        self.last_prompt = to_spoken(reply)
        return VoiceTurn(
            reply=self.last_prompt,
            notes=("agent",),
            handled_by="agent",
            state=self._state(),
        )

    # -- silence ----------------------------------------------------------

    def handle_silence(self) -> VoiceTurn:
        """Handle a turn in which the caller said nothing.

        Silence is not an empty message. On a text interface an empty
        message means the user pressed enter. Here it can mean the
        recognizer cut the caller off, the line dropped, the caller put
        the phone down, or the caller fell asleep. None of those should
        produce "please type something".
        """
        self.silent_turns += 1
        if self.silent_turns >= MAX_SILENT_TURNS:
            self.ended = True
            return VoiceTurn(
                reply=to_spoken(HANDOFF_REPLY),
                ended=True,
                escalated=True,
                notes=("silence_repeated",),
                handled_by="silence",
                state=self._state(),
            )
        return VoiceTurn(
            reply=to_spoken(UNHEARD_REPLY),
            notes=("silence",),
            handled_by="silence",
            state=self._state(),
        )

    # -- DTMF -------------------------------------------------------------

    def handle_dtmf(self, digit: str) -> VoiceTurn:
        """Handle a keypad press.

        This is the accessibility path. A caller who cannot speak, is not
        heard, or does not want to talk to a machine must still be able
        to complete a screen, because a voice-only benefits line that
        excludes them is a correctness problem, not a usability
        preference.

        The keypad path is small on purpose:

        * ``1``, ``2``, ``3`` select the first, second, and third intake
          field.
        * ``*`` repeats the last prompt.
        * ``#`` confirms the current field.
        * ``0`` reaches a person.
        """
        if self.ended:
            return VoiceTurn(
                reply="",
                ended=True,
                notes=("call_already_ended",),
                handled_by="terminated",
            )

        key = (digit or "").strip()

        if key == DTMF_HUMAN:
            self.ended = True
            return VoiceTurn(
                reply=to_spoken(REQUESTED_HANDOFF_REPLY),
                ended=True,
                escalated=True,
                notes=("dtmf_human_requested",),
                handled_by="dtmf",
                state=self._state(),
            )

        if key == DTMF_REPEAT:
            return VoiceTurn(
                reply=self.last_prompt or to_spoken(UNHEARD_REPLY),
                notes=("dtmf_repeat",),
                handled_by="dtmf",
                state=self._state(),
            )

        if key == DTMF_CONFIRM:
            self.confirmation_attempts += 1
            if should_escalate(self.confirmation_attempts):
                self.ended = True
                return VoiceTurn(
                    reply=to_spoken(HANDOFF_REPLY),
                    ended=True,
                    escalated=True,
                    outcome=ConfirmationOutcome.UNCLEAR,
                    notes=("dtmf_confirmation_limit",),
                    handled_by="dtmf",
                    state=self._state(),
                )
            return VoiceTurn(
                reply=to_spoken("Thank you. What is the next answer?"),
                outcome=ConfirmationOutcome.CONFIRMED,
                notes=("dtmf_confirm",),
                handled_by="dtmf",
                state=self._state(),
            )

        if key in ("1", "2", "3"):
            index = int(key) - 1
            if index < len(DTMF_FIELD_ORDER):
                field_name = DTMF_FIELD_ORDER[index]
                label = DTMF_FIELD_LABELS[field_name]
                self.last_prompt = to_spoken(f"Please tell me {label}.")
                return VoiceTurn(
                    reply=self.last_prompt,
                    notes=(f"dtmf_select:{field_name}",),
                    handled_by="dtmf",
                    state=self._state(),
                )

        return VoiceTurn(
            reply=to_spoken(
                "That was not one of the options. Press 1, 2, or 3 to "
                "choose a question, star to repeat, pound to confirm, or "
                "0 to talk to a person."
            ),
            notes=("dtmf_unrecognized",),
            handled_by="dtmf",
            state=self._state(),
        )

    # -- confirmation -----------------------------------------------------

    def classify(self, spoken: str) -> ConfirmationOutcome:
        """Classify a spoken reply as a confirmation."""
        outcome = classify_confirmation(spoken)
        if outcome is ConfirmationOutcome.UNCLEAR:
            self.confirmation_attempts += 1
        elif outcome is ConfirmationOutcome.CONFIRMED:
            self.confirmation_attempts = 0
        return outcome

    def should_reprompt(self) -> bool:
        """Whether to ask the caller to confirm again."""
        return should_reprompt(self.confirmation_attempts)

    def should_escalate(self) -> bool:
        """Whether the call should be handed to a person."""
        return should_escalate(self.confirmation_attempts)

    # -- helpers ----------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        """Return the current assessment state as a plain dict."""
        try:
            return self.agent.state.model_dump(mode="json")
        except Exception:
            return {}


__all__ = [
    "VoiceChannel",
    "VoiceTurn",
    "to_spoken",
    "MAX_SILENT_TURNS",
]
