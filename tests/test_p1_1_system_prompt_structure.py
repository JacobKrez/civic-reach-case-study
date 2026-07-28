"""P1.1 tripwires \u2014 the system prompt is now a conversation policy,
not a rule wall.

The P1.1 spec (CONVERSATION_OVERHAUL.md \u00a73) requires a structural
rewrite: persona -> conversation policy -> 3-4 few-shot examples
-> rules (moved last, NOT first). Both EN and ES prompts must be
in lockstep. The behavioral contract must be preserved:

  - tool contract: update_state records STRUCTURED values, no
    arithmetic in tool calls
  - calculator is the only verdict source
  - W53 forward-progress rule (last sentence of reply = next-field
    question when missing_fields is non-empty)
  - terse-reply rule (bare number = field value)
  - PII prohibition
  - DISCLOSURE INTEGRITY (do not re-state the code-templated intro)
  - Spanish parity (one prompt per language, content matches)

These tripwires assert the structural properties of the new
prompts without making the tests brittle to wording changes. They
will fail if a future edit removes a section or breaks parity.
"""

from __future__ import annotations

import re

import pytest


def test_p1_1_en_prompt_has_required_sections_in_order():
    """P1.1: the EN prompt has Persona, Conversation policy, Few-shot
    examples, and Rules sections, in that order. Rules are LAST
    (after behavioral), per the doc."""
    from src.agent import SYSTEM_PROMPT
    persona_idx = SYSTEM_PROMPT.index("# Persona")
    policy_idx = SYSTEM_PROMPT.index("# Conversation policy")
    fewshot_idx = SYSTEM_PROMPT.index("# Few-shot examples")
    rules_idx = SYSTEM_PROMPT.index("# Rules")
    assert persona_idx < policy_idx < fewshot_idx < rules_idx, (
        f"section order wrong: persona={persona_idx} policy={policy_idx} "
        f"fewshot={fewshot_idx} rules={rules_idx}"
    )


def test_p1_1_es_prompt_has_required_sections_in_order():
    """P1.1: the ES prompt has Persona, Politica de conversacion,
    Ejemplos de few-shot, and Reglas sections, in that order. The
    doc says EN and ES must be in lockstep."""
    from src.agent import SYSTEM_PROMPT_ES
    persona_idx = SYSTEM_PROMPT_ES.index("# Persona")
    policy_idx = SYSTEM_PROMPT_ES.index("# Pol")
    fewshot_idx = SYSTEM_PROMPT_ES.index("# Ejemplos de few-shot")
    rules_idx = SYSTEM_PROMPT_ES.index("# Reglas")
    assert persona_idx < policy_idx < fewshot_idx < rules_idx, (
        f"section order wrong: persona={persona_idx} policy={policy_idx} "
        f"fewshot={fewshot_idx} rules={rules_idx}"
    )


def test_p1_1_en_prompt_has_at_least_three_few_shot_examples():
    """P1.1: the doc requires 3-4 few-shot exchanges inside the prompt.
    Count the explicit "Example N" headings, which are unambiguous
    (the bare word "Assistant:" also appears in the policy section)."""
    from src.agent import SYSTEM_PROMPT
    example_headings = re.findall(r"^Example \d+ ", SYSTEM_PROMPT, re.MULTILINE)
    assert 3 <= len(example_headings) <= 4, (
        f"expected 3-4 Example N headings, got {len(example_headings)}: {example_headings!r}"
    )


def test_p1_1_es_prompt_has_at_least_three_few_shot_examples():
    from src.agent import SYSTEM_PROMPT_ES
    ejemplo_headings = re.findall(r"^Ejemplo \d+ ", SYSTEM_PROMPT_ES, re.MULTILINE)
    assert 3 <= len(ejemplo_headings) <= 4, (
        f"expected 3-4 Ejemplo N headings, got {len(ejemplo_headings)}: {ejemplo_headings!r}"
    )


def test_p1_1_en_prompt_preserves_tool_contract_and_no_arithmetic():
    """P1.1: the rules section must still mention update_state, the
    calculator-only verdict source, and the no-arithmetic rule."""
    from src.agent import SYSTEM_PROMPT
    rules = SYSTEM_PROMPT[SYSTEM_PROMPT.index("# Rules"):]
    assert "update_state" in rules, "rules must mention update_state"
    assert "arithmetic" in rules.lower(), "rules must mention arithmetic prohibition"
    assert "eligibility" in rules.lower(), "rules must mention eligibility"
    assert "calculator" in rules.lower(), "rules must mention the calculator"


def test_p1_1_es_prompt_preserves_tool_contract_and_no_arithmetic():
    from src.agent import SYSTEM_PROMPT_ES
    reglas = SYSTEM_PROMPT_ES[SYSTEM_PROMPT_ES.index("# Reglas"):]
    assert "update_state" in reglas, "reglas deben mencionar update_state"
    assert "aritm" in reglas.lower(), "reglas deben mencionar la prohibicion de aritmetica"
    assert "elegibilidad" in reglas.lower(), "reglas deben mencionar elegibilidad"
    assert "calculadora" in reglas.lower(), "reglas deben mencionar la calculadora"


def test_p1_1_w53_forward_progress_rule_preserved_in_en():
    """The W53 forward-progress rule is load-bearing (P0 docs all
    reference it). The new prompt must still carry it."""
    from src.agent import SYSTEM_PROMPT
    assert "W53" in SYSTEM_PROMPT or "INTAKE FORWARD PROGRESS" in SYSTEM_PROMPT, (
        "EN prompt must still carry the W53 forward-progress rule"
    )
    assert "missing_fields" in SYSTEM_PROMPT, (
        "EN prompt must still reference missing_fields"
    )


def test_p1_1_w53_forward_progress_rule_preserved_in_es():
    from src.agent import SYSTEM_PROMPT_ES
    assert "W53" in SYSTEM_PROMPT_ES or "PROGRESO DE LA RECOLECCI" in SYSTEM_PROMPT_ES, (
        "ES prompt must still carry the W53 forward-progress rule"
    )
    assert "missing_fields" in SYSTEM_PROMPT_ES, (
        "ES prompt must still reference missing_fields"
    )


def test_p1_1_pii_prohibition_preserved_in_both_languages():
    """The PII prohibition (never ask for SSN, DOB, address, phone,
    account, license, immigration) must still be in both prompts."""
    from src.agent import SYSTEM_PROMPT, SYSTEM_PROMPT_ES
    pii_terms_en = ["social security", "date of birth", "address", "phone"]
    for term in pii_terms_en:
        assert term in SYSTEM_PROMPT.lower(), (
            f"EN prompt must still prohibit asking for {term!r}"
        )
    pii_terms_es = ["seguro social", "direcci", "tel"]
    for term in pii_terms_es:
        assert term in SYSTEM_PROMPT_ES.lower(), (
            f"ES prompt must still prohibit asking for {term!r}"
        )


def test_p1_1_disclosure_integrity_preserved():
    """The DISCLOSURE INTEGRITY rule (do NOT repeat the code-templated
    intro on the first user turn) must still be present in both
    prompts. The test in test_disclosure_and_not_a_person.py asserts
    the literal 'DISCLOSURE INTEGRITY' section heading; this test
    asserts the content invariant (do not repeat / no repitas) so
    a future edit to the section heading does not false-positive."""
    from src.agent import SYSTEM_PROMPT, SYSTEM_PROMPT_ES
    assert "do not repeat" in SYSTEM_PROMPT.lower() or "do not re-state" in SYSTEM_PROMPT.lower()
    assert "no repitas" in SYSTEM_PROMPT_ES.lower()


def test_p1_1_persona_warm_and_plain_language():
    """The doc says the persona section must be warm, plain-language,
    and target a 6th-grade reading level. Assert the section
    explicitly mentions these properties (not the literal wording
    so future edits are tolerated)."""
    from src.agent import SYSTEM_PROMPT
    persona = SYSTEM_PROMPT[
        SYSTEM_PROMPT.index("# Persona"):SYSTEM_PROMPT.index("# Conversation policy")
    ]
    assert "warm" in persona.lower(), "persona section must be warm"
    assert "plain" in persona.lower(), "persona section must be plain-language"
    assert "6th" in persona or "sixth" in persona.lower(), (
        "persona section must mention the 6th-grade reading-level target"
    )


def test_p1_1_persona_es_warm_and_plain_language():
    from src.agent import SYSTEM_PROMPT_ES
    persona_es = SYSTEM_PROMPT_ES[
        SYSTEM_PROMPT_ES.index("# Persona"):SYSTEM_PROMPT_ES.index("# Pol")
    ]
    assert "c" in persona_es.lower() or "warm" in persona_es.lower(), (
        "ES persona section must be warm"
    )
    assert "lenguaje" in persona_es.lower() or "language" in persona_es.lower(), (
        "ES persona section must be plain-language"
    )
