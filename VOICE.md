# FoodPath Voice Channel

A spoken (IVR) front end for the existing eligibility screener.

**Status:** prototype. It has never handled a real call. There is no
telephony integration, no speech recognition, and no text-to-speech in
this repository. What exists is the part that decides what the agent
says and how a spoken reply is interpreted, and that part is where the
safety arguments live.

## What this is

`src/voice/` adapts the existing `FoodPathAgent` to a spoken channel.
It does not reimplement the agent. Every intake behavior, every
eligibility calculation, and every citation rule stays where it already
is. The voice channel only changes how a turn arrives and how the reply
is spoken.

That separation is the whole design. A second implementation of the
intake logic would be a second thing to keep correct, and FoodPath's
safety argument rests on there being exactly one place where a fact is
recorded.

| Module | Does |
|---|---|
| `src/voice/crisis_voice.py` | The spoken rendering of the crisis response. |
| `src/voice/confirmation.py` | The spoken confirmation protocol. |
| `src/voice/channel.py` | The channel: guardrail dispatch, silence handling, DTMF. |

## Three things a voice channel changes

### 1. "Call 911" is the wrong instruction on a phone

The text channel's crisis response says "call 911 now", and its own
comment explains why: *"the phrasing is written for a text interface,
there is no phone leg, so 'call 911 now', not 'hang up'."*

That reasoning inverts on a voice channel. The caller is already
holding a phone to their ear, on a non-emergency line. Telling them to
"call 911" asks them to end the call they are on in order to make a
different one, during the moment they most need to act. The voice
rendering says **hang up and call 911**.

The rest of the register follows: no markdown bullets (a screen reader
or TTS engine reads "- " as "dash"), no parentheses, no URLs, and 988
is "call 988" rather than "call or text 988" because the caller is
already calling.

The hotline numbers are imported from `CRISIS_RESOURCES`, not retyped,
so the voice and text channels can never drift apart.

### 2. Silence is not an empty message

On a text interface an empty message means the user pressed enter. On a
voice line, silence can mean the recognizer cut the caller off, the
line dropped, the caller put the phone down, or the caller fell asleep.

Treating silence as an empty message would send an empty string into
the agent and produce a reply telling the caller to type something.
`VoiceChannel.handle_silence()` handles it as its own case, re-prompts
a bounded number of times, and then hands off to a person.

### 3. A misheard confirmation is a wrong benefits record

The confirmation protocol is deliberately asymmetric:

* **A rejection beats a confirmation.** Any reply containing a negation
  is a rejection, even if it also contains a confirmation token. "no
  that's not right" contains "right", which is a confirmation token,
  and reading it as a confirmation records a value the caller
  rejected.
* **A partial match is never a confirmation.** "ye" is what a truncated
  "yes" sounds like. Accepting it would record a value the caller never
  confirmed.
* **Anything unrecognized is UNCLEAR**, and UNCLEAR re-prompts.

After three attempts, a person takes over. A caller who cannot get a
clear confirmation out of the machine should not keep pressing against
a machine that is not understanding them.

## DTMF is an accessibility path

A voice-only benefits line excludes callers who cannot speak: a caller
with a speech disability, a caller on a line that drops audio, a caller
somewhere where speaking aloud is unsafe, a caller who simply prefers
not to talk to a machine. None of those callers should be locked out of
a food-assistance screen because the only input method is speech.
That is a correctness problem, not a usability preference.

So the channel implements a real, deliberately small keypad path:

| Key | Action |
|---|---|
| `1`, `2`, `3` | Select the first, second, or third intake field |
| `*` | Repeat the last prompt |
| `#` | Confirm the current field |
| `0` | Reach a person |

It is minimal rather than pretending to be a full telephony IVR,
because a half-built keypad path that silently fails is worse than a
small one that works.

## Guardrail parity

The voice channel cannot bypass a guardrail the text channel has. It
calls the same detector functions the agent calls, in the same order:

1. **Crisis** first, because a message can match both the crisis
   detector and the injection detector, and answering a crisis with an
   injection refusal is the worst outcome this system can produce.
2. **Injection** second, because a caller steering the agent must be
   refused before the agent reasons about what they asked for.
3. **Not-a-person** third, because a caller who asks whether they are
   talking to a person needs the same answer they would get by text.
4. Everything else goes to the agent, unchanged.

`tests/voice/test_channel.py::TestGuardrailParity` asserts this
directly, including that an injection turn never reaches the LLM.

## What this does not do

* **No telephony.** No SIP, no Twilio, no call recording, no transfer
  to a queue. The handoff to a person is in-process.
* **No speech recognition.** The transcript is an input to this
  repository, not part of it. How well the recognizer produces it is
  upstream, and a voice channel inherits the recognizer's errors
  directly, which is the reason the confirmation protocol is as
  conservative as it is.
* **No text-to-speech.** The output is text written for a speech
  engine, normalized for speech, but nothing synthesizes it.
* **No Spanish parity.** The crisis rendering and the confirmation
  protocol are language-aware, but the DTMF field labels are
  English-only, and the agent's Spanish intake path has not been
  exercised through the voice channel.
* **No DTMF entry for every field.** Three fields, three digits. A
  longer form would need a different key layout.
* **No caller identification.** Nobody is authenticated, and no call
  detail is collected.
* **Never tested against a real call.** No shadow deployment, no real
  audio, no real recognizer.

## Tests

```bash
python3 -m pytest tests/voice/ -q
```

103 tests, no provider API key required, no network. They cover the
crisis rendering, the confirmation protocol, the guardrail parity, the
silence path, the DTMF path, and the spoken-register normalizer.

## A correctness bug this work surfaced

The knowledge base carried FY2026 income limits whose provenance expired
2026-09-30, and FY2027 standards took effect 2026-10-01. The repo's own
provenance test had been failing since then, and the calculator was
screening households against the prior year's thresholds, which can
wrongly deny benefits to a household that qualifies.

The tables are now FY2027, sourced from the USDA FNS COLA tables, and
the worked example in the knowledge base was recomputed to match
($1,231 net income became $1,223 under the FY2027 standard deduction).

The bug predates the voice channel and was not caused by it. It was
found because the voice channel work required the quality gate to be
green in order to ship, and the gate was red.
