"""Lightweight TF-IDF retrieval over the knowledge_base/ directory.

This module implements `retrieve(query, k)` which:
  1. Loads every `*.md` file under `knowledge_base/`.
  2. Splits each document into paragraph-sized chunks.
  3. Scores each chunk against the query using a hand-rolled TF-IDF
     cosine similarity scorer (no sklearn dependency).
  4. Returns the top-k chunks as dicts: {doc, snippet, score}.

Why hand-rolled: the project's `requirements.txt` does not list scikit-learn.
Hand-rolling keeps the dependency surface small and makes the scoring
behavior inspectable in this educational case-study repo.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from collections import Counter
from typing import Dict, List

# Default location of the knowledge base directory relative to this file:
#   <repo>/src/retrieval.py  ->  <repo>/knowledge_base/
_DEFAULT_KB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
)

# A small English stopword list. Keeping it short is fine: precision matters
# more than recall for this grounding corpus, and a tiny stopword list still
# filters the bulk of noise without accidentally throwing away domain terms.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else",
    "of", "to", "in", "on", "at", "by", "for", "with", "without",
    "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its",
    "as", "from", "into", "about", "over", "under",
    "you", "your", "they", "their", "we", "our", "i", "me", "my",
    "he", "she", "his", "her",
    "do", "does", "did", "can", "could", "should", "would", "will",
    "may", "might", "must",
    "not", "no", "yes",
    # Spanish function words used by localized queries. Domain nouns are
    # translated below; these should not dilute the bilingual query vector.
    "cual", "cuales", "es", "el", "la", "los", "las", "de", "para", "un", "una",
}

# Currency and percent punctuation carry no retrieval meaning. Keeping them
# attached made ``130%`` fail to match ``130 percent`` and produced awkward
# currency tokens. Words and each numeric component are indexed separately.
_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+")

# Do not ground answers on coincidental/zero-overlap chunks.
_MIN_SCORE = 0.15

# ---------------------------------------------------------------------------
# Spanish-language detection + localized retrieval preference (W15)
# ---------------------------------------------------------------------------
# A simple heuristic is deliberate for this PoC: the goal is to prefer the
# Spanish KB translation when the user writes in Spanish, not to be a
# general-purpose language identifier. The signal combines:
#   1. Spanish-specific characters (¿ ¡ ñ á é í ó ú ü) — a strong signal
#      because none of them appear in ordinary English prose.
#   2. High-frequency Spanish function words as whole tokens (el la los de
#      que en y es mi tu su por para con una un ...). These are matched on
#      word boundaries so English words ("my", "in") never collide.
# A message needs EITHER one Spanish-specific character OR >= 2 Spanish
# function words to be classified as Spanish. This keeps short English
# messages like "I need help" (zero Spanish markers) on the English path
# while catching accented Spanish ("¿Cuál es el límite?") and unaccented
# Spanish ("cuantos anos tiene tu abuela") alike.

_SPANISH_CHAR_RE = re.compile(r"[¿¡ñáéíóúü]", re.IGNORECASE)

_SPANISH_WORDS: frozenset[str] = frozenset({
    "el", "la", "los", "las", "de", "que", "en", "y", "es", "por", "para",
    "con", "una", "un", "mi", "tu", "su", "mis", "tus", "sus", "del", "al",
    "como", "cuando", "donde", "cuanto", "cuantos", "cuanta", "cuantas",
    "cual", "cuales", "quien", "quienes", "tiene", "tienen", "tengo",
    "somos", "estoy", "estamos", "hay", "personas", "hogar", "ingresos",
    "anos", "mes", "ayuda", "alimentos", "cupones",
})

# Whole-word token pattern for the heuristic (letters only, including
# accented ones, lowercased by caller).
_SPANISH_WORD_RE = re.compile(r"[a-záéíóúüñ]+")


def detect_spanish(text: str) -> bool:
    """Return ``True`` when *text* is likely written in Spanish.

    Heuristic, PoC-grade: one Spanish-specific character OR >= 2 Spanish
    function words (whole-token). Never raises; non-string / empty input
    returns ``False``.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    if _SPANISH_CHAR_RE.search(text):
        return True
    tokens = _SPANISH_WORD_RE.findall(text.lower())
    spanish_hits = sum(1 for t in tokens if t in _SPANISH_WORDS)
    return spanish_hits >= 2


# Score multiplier applied to chunks from a ``*.es.md`` document when the
# query is detected as Spanish. A multiplier (not a hard filter) keeps the
# English source docs eligible as a fallback: the Spanish KB is a 1:1
# translation of only the overview doc, so a Spanish query about income
# limits must still be able to surface the English income-limits doc. The
# boost is large enough that a translated chunk wins whenever it is
# topically relevant, without ever excluding the fuller English corpus.
_ES_DOC_BOOST = 1.5


def _is_spanish_doc(fname: str) -> bool:
    """Return ``True`` for localized Spanish KB docs (``*.es.md``)."""
    return fname.endswith(".es.md")


def _tokenize(text: str) -> List[str]:
    """Lowercase, split into tokens, drop stopwords and very short tokens."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = [t.lower() for t in _TOKEN_RE.findall(ascii_text)]
    aliases = {
        "ingresos": "income", "ingreso": "income", "limite": "limit",
        "hogar": "household", "bruto": "gross", "mensual": "monthly",
    }
    expanded = [aliases[t] if t in aliases else t for t in tokens]
    return [t for t in expanded if t not in _STOPWORDS and len(t) > 1]


def _split_chunks(text: str) -> List[str]:
    """Split a document into paragraph-sized chunks.

    Strategy:
      - Split on blank lines (Markdown paragraphs / table blocks).
      - Drop the leading source-metadata block from each document's first
        chunk — it doesn't help retrieval but it does inflate chunk length.
        The block may contain multiple `Source:` lines and wrapped URLs.
      - Drop empty chunks.
      - If a "paragraph" is a Markdown table line (starts with '|'), keep
        it together with the surrounding paragraph (tables are often the
        only place income-limit numbers live, so we don't want to break
        them up).
    """
    # Strip the leading source-metadata block if present. Some KB documents
    # have multiple source lines, and some wrap a source URL over several
    # lines; removing only the first line leaves a bare URL as retrievable
    # content. The metadata block is terminated by the first blank line.
    lines = text.splitlines()
    if lines and lines[0].strip().lower().startswith((
        "source:",
        "federal source:",
        "north carolina bbce source:",
        "deduction amounts:",
        "fuente:",
    )):
        for index, line in enumerate(lines):
            if not line.strip():
                lines = lines[index + 1 :]
                break
        else:
            lines = []
    body = "\n".join(lines)

    # Split into blocks on blank lines.
    raw_blocks = re.split(r"\n\s*\n", body)

    chunks: List[str] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        # If a block is huge (multi-paragraph merged), split it further on
        # single newlines so snippets stay paragraph-sized.
        if len(block) > 1500:
            for sub in re.split(r"\n", block):
                sub = sub.strip()
                if sub:
                    chunks.append(sub)
        else:
            chunks.append(block)

    # If we ended up with nothing (e.g. empty doc after stripping source),
    # fall back to the whole body so the doc is still retrievable.
    if not chunks and body.strip():
        chunks.append(body.strip())

    return chunks


def _table_index_text(chunk: str) -> str | None:
    """Return compact searchable prose for a Markdown income-limit row."""
    match = re.fullmatch(
        r"\|\s*(\d+)\s*\|\s*\$?([\d,]+(?:\.\d+)?)\s*\|"
        r"\s*\$?([\d,]+(?:\.\d+)?)\s*\|\s*\$?([\d,]+(?:\.\d+)?)\s*\|",
        chunk.strip(),
    )
    if not match:
        return None
    size, gross, net, expanded = match.groups()
    return (
        f"NC FNS income limit table household size {size} "
        f"gross 130 percent {gross} net 100 percent {net} "
        f"expanded 200 percent {expanded}"
    )


def _load_corpus(kb_dir: str) -> List[Dict]:
    """Load every *.md file under kb_dir into a list of chunk records."""
    records: List[Dict] = []
    if not os.path.isdir(kb_dir):
        return records

    for fname in sorted(os.listdir(kb_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(kb_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue

        chunks = _split_chunks(text)
        # Add one independently scored record per income-table data row.
        # The original table block remains available, while compact rows avoid
        # its large cosine norm and let household-size lookups rank directly.
        table_rows = [line.strip() for chunk in chunks for line in chunk.splitlines()
                      if _table_index_text(line)]
        for chunk in [*chunks, *table_rows]:
            # Markdown income tables are otherwise dominated by unrelated
            # numeric tokens. Index every data row as a compact semantic row
            # while preserving the original row as the returned snippet.
            index_text = _table_index_text(chunk) or chunk
            tokens = _tokenize(index_text)
            if not tokens:
                continue
            records.append(
                {
                    "doc": fname,
                    "snippet": chunk,
                    "tokens": tokens,
                }
            )
        if table_rows:
            # Generic table questions need a compact record whose snippet is
            # the actual table rows, not a heading or deductions prose.
            records.append(
                {
                    "doc": fname,
                    "snippet": (
                        "NC FNS monthly income limit table — household size, "
                        "gross 130%, net 100%, expanded 200%\n" + "\n".join(table_rows)
                    ),
                    "tokens": _tokenize(
                        "NC FNS gross monthly income limit table household size "
                        "gross 130 percent net 100 percent expanded 200 percent"
                    ),
                }
            )
    return records


# Module-level cache so we only read + tokenize files once per process. The
# cached records also carry corpus-wide IDF-weighted vectors.
_CORPUS_CACHE: Dict[str, List[Dict]] = {}


def _get_corpus(kb_dir: str) -> List[Dict]:
    if kb_dir not in _CORPUS_CACHE:
        records = _load_corpus(kb_dir)
        df_counter: Counter = Counter()
        for rec in records:
            df_counter.update(set(rec["tokens"]))
        n_docs = len(records)
        idf = {term: _idf(term, df_counter, n_docs) for term in df_counter}
        for rec in records:
            rec["idf"] = idf
            rec["vector"] = _tfidf_vector(rec["tokens"], idf)
        _CORPUS_CACHE[kb_dir] = records
    return _CORPUS_CACHE[kb_dir]


def _idf(term: str, df_counter: Counter, n_docs: int) -> float:
    """Inverse document frequency with smoothing (sklearn convention)."""
    df = df_counter.get(term, 0)
    # smoothed idf: log((1 + n) / (1 + df)) + 1
    return math.log((1 + n_docs) / (1 + df)) + 1.0


def _tfidf_vector(tokens: List[str], idf: Dict[str, float]) -> Dict[str, float]:
    """Compute a TF-IDF vector as a sparse dict {term: weight}."""
    tf = Counter(tokens)
    vec: Dict[str, float] = {}
    # Sub-linear TF scaling: 1 + log(tf) when tf > 0.
    for term, count in tf.items():
        weight = (1.0 + math.log(count)) * idf.get(term, 0.0)
        if weight > 0:
            vec[term] = weight
    return vec


def _norm(vec: Dict[str, float]) -> float:
    return math.sqrt(sum(v * v for v in vec.values()))


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Cosine similarity between two sparse dict vectors."""
    if not a or not b:
        return 0.0
    # Iterate over the smaller dict for efficiency.
    if len(a) > len(b):
        a, b = b, a
    dot = 0.0
    for term, weight in a.items():
        if term in b:
            dot += weight * b[term]
    denom = _norm(a) * _norm(b)
    if denom == 0.0:
        return 0.0
    return dot / denom


def retrieve(query: str, k: int = 3, kb_dir: str | None = None) -> List[Dict]:
    """Return the top-k chunks for `query` as a list of dicts.

    Each dict has keys:
        - doc: filename (basename) of the source markdown file
        - snippet: the paragraph-sized text chunk
        - score: float cosine similarity in [0, 1]

    Never raises on empty/odd input — returns an empty list if the corpus
    is empty or the query has no tokens after tokenization.
    """
    if k <= 0:
        return []

    directory = kb_dir or _DEFAULT_KB_DIR
    corpus = _get_corpus(directory)
    if not corpus:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    # IDF and document vectors are precomputed in _get_corpus.
    idf = corpus[0]["idf"]
    q_vec = _tfidf_vector(query_tokens, idf)

    # Sort by score desc, then by doc name to keep results stable.
    scored: List[Dict] = []
    for rec in corpus:
        d_vec = rec["vector"]
        score = _cosine(q_vec, d_vec)
        scored.append(
            {
                "doc": rec["doc"],
                "snippet": rec["snippet"],
                "score": score,
            }
        )
    scored.sort(key=lambda r: (-r["score"], r["doc"]))

    # Spanish localization (W15). When the query reads as Spanish, surface
    # the translated *.es.md overview as ONE grounding chunk while reserving
    # the remaining top-k slots for the fuller corpus. Two forces shape this:
    #   1. TF-IDF already ranks the translation first for a Spanish question
    #      (its Spanish chunk tokens match the query), so the translation is
    #      genuinely "considered" — we keep the best-scoring .es.md chunk.
    #   2. But the translation covers ONLY the program-overview doc, so an
    #      unbounded number of its stopword-heavy chunks would crowd the
    #      English income-limits / deductions / apply docs out of the top-k
    #      for a Spanish income question. Capping the translation to a single
    #      slot keeps the English fallback reachable for the topics the
    #      translation does not cover. detect_spanish() is the public
    #      heuristic hook; callers may also branch on it directly.
    relevant = [row for row in scored if row["score"] >= _MIN_SCORE]

    if detect_spanish(query):
        # Keep one genuinely overlapping localized block even when its score is
        # below the global English-corpus floor, then fill with relevant
        # English fallbacks. This preserves localized context without admitting
        # zero-score citations.
        best_es = next(
            (row for row in scored if _is_spanish_doc(row["doc"]) and row["score"] > 0.0),
            None,
        )
        localized = list(relevant)
        if best_es is not None and best_es not in localized:
            localized.append(best_es)
            localized.sort(key=lambda r: (-r["score"], r["doc"]))
        localized = _cap_doc_chunks(localized, k)
        # Reserve one slot for the localized block when it exists.
        if best_es is not None and best_es not in localized[:k]:
            localized = [best_es, *[r for r in localized if r is not best_es]]
        return localized[:k]

    return relevant[:k]


def _cap_doc_chunks(scored: List[Dict], k: int, max_es_chunks: int = 1) -> List[Dict]:
    """Return *scored* with at most *max_es_chunks* chunks from *.es.md docs.

    Ordering is preserved (the list stays score-sorted); only the NUMBER of
    Spanish-translation chunks is capped so the rest of the corpus fills the
    remaining top-k slots. English (*.md) chunks are never capped.
    """
    out: List[Dict] = []
    es_seen = 0
    for r in scored:
        if _is_spanish_doc(r["doc"]):
            if es_seen >= max_es_chunks:
                continue
            es_seen += 1
        out.append(r)
    return out


__all__ = ["retrieve", "detect_spanish"]
