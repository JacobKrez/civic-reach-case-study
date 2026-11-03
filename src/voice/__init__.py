"""The voice (IVR) channel for FoodPath.

A spoken front end for the existing eligibility screener.

The constraint that shapes every module here: a message that will be
read aloud cannot use the conventions of a chat bubble. Bullets become
dashes, parentheses become noise, and "call 911 now" is the wrong
instruction for someone already holding a phone.

The four modules:

``src.voice.crisis_voice``
    The spoken rendering of the crisis response. Different from the text
    rendering in register and in the one instruction that matters.
``src.voice.confirmation``
    The spoken confirmation protocol. Asymmetric on purpose: a
    rejection beats a confirmation, and a partial match is never a
    confirmation.
``src.voice.channel``
    The channel itself. Adapts ``FoodPathAgent`` rather than
    reimplementing it, calls the same guardrail detectors the agent
    calls, handles silence as its own signal, and provides a DTMF
    accessibility path.
``src.voice.text_to_speech``
    The spoken-register normalizer.
"""

from .channel import VoiceChannel, VoiceTurn, to_spoken
from .confirmation import (
    ConfirmationOutcome,
    classify_confirmation,
    should_escalate,
    should_reprompt,
)
from .crisis_voice import voice_crisis_response

__all__ = [
    "ConfirmationOutcome",
    "VoiceChannel",
    "VoiceTurn",
    "classify_confirmation",
    "should_escalate",
    "should_reprompt",
    "to_spoken",
    "voice_crisis_response",
]
