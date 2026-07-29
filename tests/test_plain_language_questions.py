"""Plain-language contracts for the English intake questions."""

from src.agent import FIRST_TURN_GREETING_EN, SYSTEM_PROMPT
from src.agent_questions import NEXT_FIELD_QUESTION


def test_household_question_is_simple_but_keeps_the_food_household_definition():
    question = NEXT_FIELD_QUESTION["en"]["household_size"]

    assert question == (
        "How many people usually buy and prepare food with you? "
        "Include yourself. If it is just you, answer 1."
    )
    assert "including you, usually" not in question.lower()
    assert "in your home right now" not in question.lower()


def test_age_or_disability_question_asks_directly_about_people():
    question = NEXT_FIELD_QUESTION["en"]["has_elderly_or_disabled_member"]

    assert question == (
        "Now think about the people you included. "
        "Is anyone age 60 or older, or does anyone have a disability?"
    )
    assert "have an elderly" not in question.lower()
    assert "disabled member" not in question.lower()


def test_greeting_and_model_examples_use_the_plain_language_questions():
    household_question = NEXT_FIELD_QUESTION["en"]["household_size"]
    age_or_disability_question = NEXT_FIELD_QUESTION["en"][
        "has_elderly_or_disabled_member"
    ]

    assert household_question in FIRST_TURN_GREETING_EN
    assert household_question in SYSTEM_PROMPT
    assert age_or_disability_question in SYSTEM_PROMPT
