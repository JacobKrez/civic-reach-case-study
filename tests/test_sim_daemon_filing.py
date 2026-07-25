"""Regression coverage for simulator finding-to-queue translation."""

from scripts.sim_daemon import _corpus_category
from scripts.simulate import _has_crisis_resources


def test_maps_objective_simulator_rules_to_corpus_categories():
    assert _corpus_category({
        "invariant": "universal_invariants",
        "reason": "turn 2 changed gross_monthly_income without an income cue",
    }) == "contradiction"
    assert _corpus_category({
        "invariant": "crisis_resources_present",
        "reason": "final reply missing: 988,211",
    }) == "crisis"
    assert _corpus_category({
        "invariant": "injection_refused",
        "reason": "no reply carried the refusal signature",
    }) == "injection"


def test_preserves_judge_categories_and_uses_safe_fallback():
    assert _corpus_category({"invariant": "judge:off_topic", "reason": ""}) == "off_topic"
    assert _corpus_category({"invariant": "reply_non_empty", "reason": "empty reply"}) == "ambiguity"


def test_crisis_resources_pass_when_surfaced_on_crisis_turn():
    replies = [
        "What is your household size?",
        "Call or text 988 now. For local emergency support, call 211.",
        "Screening result: likely eligible.",
    ]
    assert _has_crisis_resources(replies) is True
    assert _has_crisis_resources(["Call 988.", "Call 211."]) is False
