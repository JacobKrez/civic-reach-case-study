"""W15: Spanish-language detection and localized KB retrieval.

When the user's message reads as Spanish, retrieval must surface the
translated ``*.es.md`` program-overview doc AND keep the fuller English
corpus reachable as a fallback. The translation is a 1:1 rendering of the
overview doc only, so a Spanish income-limit question must still be able to
surface the English income-limits / deductions / apply docs. The design
caps the translation to a single top-k slot (its best-scoring chunk) and
reserves the rest for the fuller corpus — see ``_cap_doc_chunks``.
"""

from __future__ import annotations

import os

import pytest

from src.retrieval import detect_spanish, retrieve


KB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
)
ES_DOC = "01_program_overview.es.md"


def _es_doc_present() -> bool:
    return os.path.isfile(os.path.join(KB_DIR, ES_DOC))


# ---------------------------------------------------------------------------
# detect_spanish() heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Spanish-specific characters (¿ ¡ ñ accents) are a strong signal.
        "¿Cuál es el límite de ingresos para FNS?",
        "¿Cómo solicito los beneficios?",
        "Mi abuela tiene 67 años.",
        "Tengo una pregunta sobre la deducción médica.",
        # Unaccented Spanish caught by function-word density (>= 2 hits).
        "cuantos anos tiene tu abuela",
        "somos cuatro personas en mi hogar",
        "quiero saber si califico para los cupones de alimentos",
    ],
)
def test_detect_spanish_positive(text):
    assert detect_spanish(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "I need help with food stamps.",
        "What is the income limit for FNS?",
        "There are three of us in my household.",
        "How much money can I make and still qualify?",
        "income limit household size",
        # English words that happen to share letters with Spanish function
        # words ("my", "in", "so") must not trip the whole-token matcher.
        "my income is in the bank so what now",
    ],
)
def test_detect_spanish_negative(text):
    assert detect_spanish(text) is False


def test_detect_spanish_empty_and_non_string():
    assert detect_spanish("") is False
    assert detect_spanish("   ") is False
    assert detect_spanish(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Retrieval surfaces the Spanish doc for Spanish queries
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _es_doc_present(), reason="Spanish KB doc must exist")
def test_spanish_query_surfaces_spanish_doc_as_top_hit():
    """A Spanish program-overview question ranks the .es.md doc first."""
    results = retrieve("¿Qué es el programa FNS y a quién sirve?", k=3)
    assert len(results) > 0
    assert results[0]["doc"] == ES_DOC


@pytest.mark.skipif(not _es_doc_present(), reason="Spanish KB doc must exist")
def test_spanish_doc_present_for_spanish_query():
    results = retrieve("¿Cómo funcionan los beneficios y la tarjeta EBT?", k=5)
    docs = [r["doc"] for r in results]
    assert ES_DOC in docs


@pytest.mark.skipif(not _es_doc_present(), reason="Spanish KB doc must exist")
def test_spanish_translation_is_capped_so_english_fallback_stays_reachable():
    """The translation covers ONLY the overview doc. A Spanish income-limit
    question shares no tokens with the English income doc, yet the English
    corpus must still occupy top-k slots so it can ground topics the
    translation does not cover. The .es.md doc is capped to one chunk; the
    remaining slots come from the fuller (English) corpus."""
    results = retrieve("cuál es el límite de ingresos para un hogar", k=5)
    docs = [r["doc"] for r in results]
    # The translation appears (as the single capped chunk)...
    assert docs.count(ES_DOC) == 1
    # ...and the rest of the slots are filled by the non-translation corpus,
    # which is where the English income-limits / deductions / apply docs live.
    assert any(not d.endswith(".es.md") for d in docs)


@pytest.mark.skipif(not _es_doc_present(), reason="Spanish KB doc must exist")
def test_spanish_doc_never_dominates_top_k():
    """No more than one .es.md chunk may appear in a Spanish query's top-k,
    regardless of how strongly its stopwords match."""
    for query in (
        "¿Qué es FNS?",
        "cuéntame sobre los beneficios de alimentos y el hogar",
        "límite de ingresos tamaño del hogar",
    ):
        docs = [r["doc"] for r in retrieve(query, k=5)]
        assert docs.count(ES_DOC) <= 1, f"{query!r}: {docs}"


@pytest.mark.skipif(not _es_doc_present(), reason="Spanish KB doc must exist")
def test_english_query_is_not_forced_to_spanish_doc():
    """An English query still ranks the English overview doc first — the
    Spanish localization path must not leak onto the English path."""
    results = retrieve("what is FNS and who does it serve program overview", k=3)
    assert len(results) > 0
    assert results[0]["doc"] == "01_program_overview.md"


@pytest.mark.skipif(not _es_doc_present(), reason="Spanish KB doc must exist")
def test_english_query_top_k_has_no_spanish_doc():
    """For an English query, the translation should not appear at all (it is
    only surfaced on the Spanish path)."""
    results = retrieve("income limit household size", k=5)
    assert all(not r["doc"].endswith(".es.md") for r in results)
