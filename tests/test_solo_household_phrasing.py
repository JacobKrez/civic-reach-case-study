""""Just me." must record a household of one.

The household question ends with "If it is just you, answer 1", so it
actively invites this phrasing, and it is the most common way a
single-person household answers. It parsed to nothing: no digit for the
numeric patterns to find, and the extraction pass skipped it too (no
digit, no household noun), which left the model's tool call as the only
path to recording it.

Live, that path failed 1 run in 3 — ``household_size`` stayed empty, the
identical question came back, and the screen could not be completed. The
user's report was "it keeps asking the question over and over".

The safety requirement is the mirror image: "just me and my kid" is a
household of TWO. Recording it as one understates the applicable income
limit and can produce a wrong denial, so any conjunction or second person
disqualifies the phrase and it falls through to the digit parsers.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent


@pytest.fixture()
def agent():
    return FoodPathAgent(llm_client=None, retriever=lambda _q: [])


SOLO = [
    "just me",
    "Just me",
    "just me.",
    "Just me!",
    "only me",
    "just myself",
    "only myself",
    "it is just me",
    "it's just me",
    "its just me",
    "nobody but me",
    "no one but me",
]

NOT_SOLO = [
    "just me and my kid",
    "just me and my mom",
    "just me, my wife",
    "just me plus my son",
    "only me and 2 kids",
    "me and my brother",
    "just my kids",
    "just me & my partner",
]


@pytest.mark.parametrize("text", SOLO)
def test_solo_phrasings_record_a_household_of_one(agent, text):
    assert agent._fallback_parse_terse_reply("household_size", text) == 1


@pytest.mark.parametrize("text", NOT_SOLO)
def test_a_second_person_is_never_read_as_a_household_of_one(agent, text):
    """Under-counting the household understates the income limit, which
    is the direction that produces a wrong denial."""
    assert agent._fallback_parse_terse_reply("household_size", text) != 1


@pytest.mark.parametrize("text", SOLO)
def test_solo_phrasings_pass_the_extraction_gate(agent, text):
    """Second line of defence: the same phrasing inside a longer
    sentence should still reach the extraction pass."""
    assert FoodPathAgent._message_plausibly_has_intake_content(text)


def test_end_to_end_just_me_completes_without_the_model_recording_it():
    """The whole point: the screen must finish even when the model
    contributes no tool call at all."""

    class _NoToolLLM:
        def chat(self, messages, system=None, tools=None):
            return {"content": "Thanks.", "tool_calls": []}

    agent = FoodPathAgent(llm_client=_NoToolLLM(), retriever=lambda _q: [])
    agent.turn("just me")
    assert agent.state.household_size == 1, (
        "with no tool call from the model, the deterministic parser is the "
        "only thing standing between the user and an unfinishable screen"
    )


def test_the_question_still_invites_this_phrasing():
    """If the wording ever stops saying 'if it is just you, answer 1',
    this test is the reminder that the parser was built around it."""
    from src.agent_questions import NEXT_FIELD_QUESTION

    question = NEXT_FIELD_QUESTION["en"]["household_size"].lower()
    assert "just you" in question or "just me" in question, question
