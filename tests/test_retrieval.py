"""Tests for the lightweight TF-IDF retrieval in src/retrieval.py.

Acceptance criteria:
  - retrieve() returns non-empty results for an on-topic query like
    'income limit household size'
  - results are ranked by score (descending)
  - an off-topic nonsense query still returns SOMETHING (graceful,
    not an exception)
"""

from __future__ import annotations

import os

import pytest

from src.retrieval import _split_chunks, retrieve


KB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
)


def _at_least_three_md_files() -> bool:
    if not os.path.isdir(KB_DIR):
        return False
    return sum(1 for f in os.listdir(KB_DIR) if f.endswith(".md")) >= 3


@pytest.mark.skipif(
    not _at_least_three_md_files(),
    reason="knowledge_base must contain at least 3 markdown docs",
)
def test_on_topic_query_returns_non_empty_results():
    """On-topic query should return at least one chunk."""
    results = retrieve("income limit household size", k=3)
    assert isinstance(results, list), "retrieve() must return a list"
    assert len(results) > 0, "on-topic query must return at least one chunk"

    # Each result must have the documented shape.
    for r in results:
        assert isinstance(r, dict)
        assert "doc" in r and isinstance(r["doc"], str) and r["doc"]
        assert "snippet" in r and isinstance(r["snippet"], str) and r["snippet"]
        assert "score" in r and isinstance(r["score"], (int, float))


@pytest.mark.skipif(
    not _at_least_three_md_files(),
    reason="knowledge_base must contain at least 3 markdown docs",
)
def test_results_are_ranked_score_descending():
    """Returned results must be sorted by score descending."""
    results = retrieve("income limit household size", k=5)
    assert len(results) >= 2, "need at least 2 results to verify ordering"

    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True), (
        f"results must be ranked by score descending; got {scores}"
    )


@pytest.mark.skipif(
    not _at_least_three_md_files(),
    reason="knowledge_base must contain at least 3 markdown docs",
)
def test_off_topic_nonsense_query_returns_no_ungrounded_sources():
    """A nonsense query must not fabricate relevance or citations."""
    nonsense = "zxqzxq blarg flarp"
    results = retrieve(nonsense, k=3)
    assert isinstance(results, list)
    assert results == []
    assert retrieve("", k=3) == []


@pytest.mark.skipif(
    not _at_least_three_md_files(),
    reason="knowledge_base must contain at least 3 markdown docs",
)
def test_top_result_for_income_query_relevant():
    """For an income-limit query, the top hit should mention income or limits."""
    results = retrieve("what is the income limit for a household", k=3)
    assert len(results) > 0
    top = results[0]
    snippet_lower = top["snippet"].lower()
    # The top hit should at least contain the word 'income' or 'limit'.
    assert ("income" in snippet_lower) or ("limit" in snippet_lower), (
        f"top hit doesn't look relevant: {top['snippet'][:120]}..."
    )


@pytest.mark.skipif(
    not _at_least_three_md_files(),
    reason="knowledge_base must contain at least 3 markdown docs",
)
def test_k_parameter_is_respected():
    """k parameter must cap the number of returned results."""
    results = retrieve("FNS SNAP eligibility", k=2)
    assert len(results) <= 2


def test_k_zero_or_negative_returns_empty():
    """Edge case: k<=0 should return an empty list, not raise."""
    assert retrieve("anything", k=0) == []
    assert retrieve("anything", k=-1) == []


def test_split_chunks_removes_all_leading_source_metadata():
    """Repeated and wrapped source headers must not become KB chunks."""
    chunks = _split_chunks(
        "Source: https://example.test/eligibility\n"
        "Source: https://example.test/fy2026.pdf (FY2026,\n"
        "effective 2025-10-01 through 2026-09-30)\n\n"
        "# Actual rule\n\n"
        "The standard deduction is $209."
    )
    joined = "\n".join(chunks)
    assert "example.test" not in joined
    assert "# Actual rule" in joined
