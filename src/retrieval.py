"""Lightweight TF-IDF retrieval over the knowledge_base/ directory.

This module implements `retrieve(query, k)` which:
  1. Loads every `*.md` file under `knowledge_base/`.
  2. Splits each document into paragraph-sized chunks.
  3. Scores each chunk against the query using a hand-rolled TF-IDF
     cosine similarity scorer (no sklearn dependency).
  4. Returns the top-k chunks as dicts: {doc, snippet, score}.

Why hand-rolled: the project's `requirements.txt` does not list scikit-learn,
Hand-rolling keeps the dependency surface small and makes the scoring
behavior inspectable in this educational case-study repo.
"""

from __future__ import annotations

import math
import os
import re
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
}

# Token pattern: word characters, allowing the % and $ signs to remain as part
# of tokens (so "$5,500" and "130%" are preserved as searchable terms).
_TOKEN_RE = re.compile(r"[A-Za-z0-9%$]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase, split into tokens, drop stopwords and very short tokens."""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _split_chunks(text: str) -> List[str]:
    """Split a document into paragraph-sized chunks.

    Strategy:
      - Split on blank lines (Markdown paragraphs / table blocks).
      - Drop the leading `Source: <url>` line from each document's first
        chunk — it doesn't help retrieval but it does inflate chunk length.
      - Drop empty chunks.
      - If a "paragraph" is a Markdown table line (starts with '|'), keep
        it together with the surrounding paragraph (tables are often the
        only place income-limit numbers live, so we don't want to break
        them up).
    """
    # Strip the leading "Source:" line if present.
    lines = text.splitlines()
    if lines and lines[0].lstrip().lower().startswith("source:"):
        lines = lines[1:]
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

        for chunk in _split_chunks(text):
            tokens = _tokenize(chunk)
            if not tokens:
                continue
            records.append(
                {
                    "doc": fname,
                    "snippet": chunk,
                    "tokens": tokens,
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
        # Graceful fallback: return the first k chunks with a small constant
        # score so an off-topic nonsense query still gets SOMETHING.
        return [
            {"doc": rec["doc"], "snippet": rec["snippet"], "score": 0.0}
            for rec in corpus[:k]
        ]

    # IDF and document vectors are precomputed in _get_corpus.
    idf = corpus[0]["idf"]
    q_vec = _tfidf_vector(query_tokens, idf)

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

    # Sort by score desc, then by doc name to keep results stable.
    scored.sort(key=lambda r: (-r["score"], r["doc"]))
    return scored[:k]


__all__ = ["retrieve"]
