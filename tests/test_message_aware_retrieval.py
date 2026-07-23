"""W9: the latest user question influences retrieval during intake."""

import json

from src.agent import CivicReachAgent


def test_deductions_question_retrieves_deductions_document():
    agent = CivicReachAgent()
    context = json.loads(agent._context(
        "What deductions are allowed for medical costs and dependent care?"
    ))
    assert any(
        hit["doc"] == "03_deductions_and_household.md"
        for hit in context["retrieved_rule_text"]
    )