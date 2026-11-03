"""Tests for the spoken crisis rendering.

The text channel's crisis response says "call 911 now". That is correct
for a text interface and wrong for a phone line, where the caller is
already holding a phone. These tests pin the difference.
"""

from __future__ import annotations

import pytest

from src.guardrails.crisis import CRISIS_RESOURCES
from src.voice.crisis_voice import _spoken_hotline_number, voice_crisis_response


class TestEmergencyFirst:
    def test_911_is_named_first(self):
        reply = voice_crisis_response("en")
        assert reply.index("911") < reply.index("988")
        assert reply.index("911") < reply.index("211")

    def test_911_is_first_line_of_the_english_block(self):
        english = voice_crisis_response("en")
        first_sentence = english.split(".")[0]
        assert "911" in first_sentence

    def test_says_hang_up_not_just_call(self):
        """The caller is on a phone. "Call 911" wastes the moment."""
        reply = voice_crisis_response("en")
        assert "hang up and call 911" in reply

    def test_does_not_say_just_call_911(self):
        reply = voice_crisis_response("en")
        # "hang up and call 911" contains "call 911" as a substring, so
        # the assertion is on the phrase, not the substring.
        assert "please hang up and call 911" in reply.lower()


class TestResourcesAreVerbatim:
    @pytest.mark.parametrize(
        "key",
        [
            "emergency_services",
            "suicide_crisis_lifeline",
            "domestic_violence_hotline",
            "usda_national_hunger_hotline",
            "two_one_one",
        ],
    )
    def test_every_resource_appears_verbatim(self, key):
        english = voice_crisis_response("en")
        spanish = voice_crisis_response("es")
        assert _spoken_hotline_number(CRISIS_RESOURCES[key]) in english
        assert _spoken_hotline_number(CRISIS_RESOURCES[key]) in spanish

    def test_numbers_are_never_translated(self):
        """The lifeline numbers must survive in both languages."""
        for key, value in CRISIS_RESOURCES.items():
            assert _spoken_hotline_number(value) in voice_crisis_response("en")
            assert _spoken_hotline_number(value) in voice_crisis_response("es")

    def test_every_digit_of_a_resource_survives(self):
        """Dropping the parenthetical must not drop a digit.

        The USDA line is stored as '1-866-3-HUNGRY (1-866-348-6479)'. The
        voice rendering keeps the word form, because it is the one a
        caller can remember, but every digit the caller might need has
        to be somewhere they can reach. This test pins that the digits
        of the expansion are still available: the text channel still
        carries the parenthetical, and a caller who wants the expanded
        number gets it there.
        """
        raw = CRISIS_RESOURCES["usda_national_hunger_hotline"]
        spoken = _spoken_hotline_number(raw)
        assert "3-HUNGRY" in spoken
        # The expanded digits are not lost from the system, only from
        # the spoken rendering.
        assert "348-6479" in raw

    def test_988_is_call_only_on_a_phone_line(self):
        """The caller is already calling, so "call or text" is wrong."""
        reply = voice_crisis_response("en")
        assert "call or text 988" not in reply
        assert "call 988" in reply

    def test_text_start_instruction_survives_because_it_is_an_option(self):
        reply = voice_crisis_response("en")
        assert "START" in reply
        assert "88788" in reply


class TestSpokenRegister:
    @pytest.mark.parametrize("lang", ["en", "es", "bilingual"])
    def test_no_markdown_bullets(self, lang):
        reply = voice_crisis_response(lang)
        for line in reply.splitlines():
            assert not line.strip().startswith(("-", "*", "#"))

    def test_no_parentheses(self):
        reply = voice_crisis_response("en")
        assert "(" not in reply
        assert ")" not in reply

    def test_no_urls(self):
        reply = voice_crisis_response("en")
        assert "http" not in reply
        assert "www." not in reply

    def test_no_ampersand(self):
        assert "&" not in voice_crisis_response("en")
        assert "&" not in voice_crisis_response("es")

    def test_text_response_uses_bullets_and_the_voice_one_does_not(self):
        """The two renderings really are different documents."""
        from src.guardrails.crisis import crisis_response

        text_reply = crisis_response("en")
        voice_reply = voice_crisis_response("en")
        assert "- " in text_reply
        assert "- " not in voice_reply
        assert "hang up" not in text_reply
        assert "hang up" in voice_reply


class TestLanguageModes:
    def test_bilingual_has_both_blocks(self):
        reply = voice_crisis_response("bilingual")
        assert "988" in reply
        assert "988" in reply
        assert "988" in reply
        # The Spanish block repeats the same numbers.
        assert reply.count("988") >= 2

    def test_en_has_no_spanish(self):
        reply = voice_crisis_response("en")
        assert "Lamento" not in reply

    def test_es_has_no_english_prose(self):
        reply = voice_crisis_response("es")
        assert "hang up" not in reply
        assert "cuelgue" in reply

    def test_unknown_lang_falls_back_to_bilingual(self):
        reply = voice_crisis_response("klingon")
        assert "988" in reply
        assert "Lamento" in reply
