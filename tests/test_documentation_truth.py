"""W66 — documentation truth regressions.

Cheap, mechanical checks that reviewer-facing documentation stays in
sync with the implementation. Each test asserts a string or
absence-of-string claim that, if it silently regresses, would cause a
reviewer to read a stale state of the repo.

These tests do not exhaustively validate every documentation claim
(the quality bar file is the source of truth for that). They exist
to make the high-traffic, high-drift facts unshippable: the W47
queue-target footgun, the W65 sanitized-evidence contract, and the
README repo-layout row.

If any of these tests fail, the right fix is to update the docstring,
commit message, or skill that introduced the regression — not to
loosen the assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVALUATION = REPO / "EVALUATION.md"
QUALITY_BAR = REPO / "QUALITY_BAR.md"
README = REPO / "README.md"


# ---------------------------------------------------------------------------
# Queuing-target invariants (W47 footgun guard)
# ---------------------------------------------------------------------------


def test_evaluation_documents_sim_daemon_queue_target():
    """EVALUATION.md must say sim_daemon auto-files into failures_queue.jsonl."""
    text = EVALUATION.read_text(encoding="utf-8")
    assert "failures_queue.jsonl" in text, (
        "EVALUATION.md must reference redteam/failures_queue.jsonl as the "
        "local triage queue the sim_daemon auto-files into."
    )


def test_evaluation_does_not_say_sim_daemon_writes_to_attack_corpus():
    """EVALUATION.md must NOT say sim_daemon writes into attack_cases.jsonl.

    The sim_daemon routes findings through file_failures.py into
    failures_queue.jsonl. Promoting a finding to the human-curated
    attack_cases.jsonl corpus is a manual triage step, not an
    automated write. A doc line like "auto-files ... into
    redteam/attack_cases.jsonl" is the W47 footgun warning sign.

    A doc line that says "the daemon does NOT write to attack_cases.jsonl"
    is the CORRECT phrasing and must remain allowed — the test only
    forbids the misleading positive phrasing.
    """
    text = EVALUATION.read_text(encoding="utf-8").lower()
    assert "attack_cases.jsonl" in text, (
        "Sanity check: attack_cases.jsonl is the human-curated corpus and "
        "should still be mentioned in EVALUATION.md (just not as the "
        "sim_daemon auto-file target)."
    )
    # The phrasings the W47 footgun described — sim_daemon writes to
    # attack_cases.jsonl. Look for the misleading patterns in the SAME
    # paragraph. We slice into paragraphs (separated by blank lines)
    # so a paragraph that happens to mention both the daemon's role
    # AND the W48 corpus in passing does NOT trip the test.
    for paragraph in re.split(r"\n\s*\n", text):
        # Allow doc lines that explicitly say the daemon DOES NOT write
        # to attack_cases.jsonl — that is the correct phrasing this
        # chunk W66 is establishing.
        defensive_phrases = (
            "does not",
            "does **not**",
            "never",
            "not write",
            "do not write",
            "won't",
            "does not auto",
        )
        if any(phrase in paragraph for phrase in defensive_phrases):
            continue
        bad_patterns = [
            r"sim_daemon[^.]*attacks?\s+to\s+`?redteam/attack_cases\.jsonl",
            r"files into\s+`?redteam/attack_cases\.jsonl",
            r"auto-?files[^.]*attack_cases\.jsonl",
            r"daemon[^.]*attack_cases\.jsonl",
        ]
        for pattern in bad_patterns:
            match = re.search(pattern, paragraph, flags=re.DOTALL)
            assert match is None, (
                f"EVALUATION.md contains the W47 footgun phrasing "
                f"{pattern!r}: sim_daemon does NOT write to "
                f"attack_cases.jsonl. Update the offending paragraph:\n\n"
                f"{paragraph[:300]!r}"
            )


def test_readme_known_limitations_distinguishes_queue_from_corpus():
    """README's 'Known limitations' must distinguish failures_queue from attack_cases.jsonl."""
    text = README.read_text(encoding="utf-8")
    # Find the bullet about "Keyword + classifier is still not exhaustive"
    # and verify it mentions the failures_queue.jsonl local triage queue
    # AND the attack_cases.jsonl human-curated corpus AND the fact that
    # auto-filers do not write to the corpus.
    keywords_section = _bullet_body(text, "Keyword + classifier is still not exhaustive")
    assert "failures_queue.jsonl" in keywords_section, (
        "README's 'Keyword + classifier is still not exhaustive' bullet "
        "must mention redteam/failures_queue.jsonl as the local triage queue."
    )
    assert "attack_cases.jsonl" in keywords_section, (
        "README's 'Keyword + classifier is still not exhaustive' bullet "
        "must mention attack_cases.jsonl as the human-curated corpus so a "
        "reader understands the promotion path."
    )
    # The old drift phrasing — "added to attack_cases.jsonl before the
    # next iteration" — is the specific claim W66 removed. Check that the
    # exact legacy phrasing is gone so a careless re-paste is caught.
    assert "added to `attack_cases.jsonl` before the" not in text.replace(
        "’", "'"
    ), (
        "README still contains the W47-footgun phrasing 'added to "
        "attack_cases.jsonl before the next iteration'. Update that bullet."
    )


def test_readme_repo_layout_lists_failures_queue():
    """README's Repository layout row must list failures_queue.jsonl."""
    text = README.read_text(encoding="utf-8")
    layout_row = _bullet_body(text, "redteam/")
    assert "failures_queue.jsonl" in layout_row, (
        "README's Repository layout row for redteam/ must list "
        "failures_queue.jsonl so reviewers know where local triage lives."
    )


# ---------------------------------------------------------------------------
# W65 sanitized-evidence + regenerator_signature contract
# ---------------------------------------------------------------------------


def test_quality_bar_g5_references_sanitized_evidence():
    """G5 source of truth must be the sanitized evidence file, not raw simulator_*.json."""
    text = QUALITY_BAR.read_text(encoding="utf-8")
    g5_section = _bullet_body(text, "G5 — simulator invariant pass rate")
    assert "simulator_sanitized_" in g5_section, (
        "QUALITY_BAR.md G5 must reference simulator_sanitized_*.json as the "
        "source of truth (W65 contract)."
    )
    assert "regenerator_signature" in g5_section, (
        "QUALITY_BAR.md G5 must mention regenerator_signature — the tripwire "
        "that detects simulator-harness drift independent of the pass rate."
    )


def test_quality_bar_g5_reproduction_command_present():
    """QUALITY_BAR.md G5 must document the in-process regeneration command."""
    text = QUALITY_BAR.read_text(encoding="utf-8")
    g5_section = _bullet_body(text, "G5 — simulator invariant pass rate")
    assert "python3 scripts/gate.py --only G5" in g5_section, (
        "QUALITY_BAR.md G5 must document the actual reproduction command "
        "`python3 scripts/gate.py --only G5` so reviewers can re-run it."
    )


def test_quality_bar_g5_threshold_summary_includes_signature_match():
    """The G5 row in QUALITY_BAR's summary table must reflect the W65 contract."""
    text = QUALITY_BAR.read_text(encoding="utf-8")
    # Locate the summary table row whose first column is "G5 simulator"
    match = re.search(
        r"\|\s*G5\s+simulator[^|]*\|[^|]*\|[^|]*\|",
        text,
        flags=re.IGNORECASE,
    )
    assert match is not None, "QUALITY_BAR.md summary table is missing the G5 simulator row."
    row = match.group(0).lower()
    assert "signature" in row or "sanitized" in row, (
        "QUALITY_BAR.md summary-table G5 row must mention signature match "
        "(or sanitized evidence) so a skim-reader sees the W65 contract."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section(text: str, header_phrase: str) -> str:
    """Return the substring from a header mention through the next blank line.

    The point is to scope asserts to "the G5 section", "the bullet about
    Keyword+classifier", etc., instead of letting a stray phrase elsewhere
    in the doc satisfy the assertion. ``header_phrase`` is a unique string
    that occurs inside the desired block; the returned text runs from that
    line until the next paragraph break (two newlines in a row) or EOF.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if header_phrase in line:
            start = index
            break
    if start is None:
        return ""
    block: list[str] = []
    for line in lines[start:]:
        block.append(line)
        if line.strip() == "" and len(block) > 1:
            # Paragraph break — stop before the blank line that ends the
            # section. Return without that trailing blank.
            return "\n".join(block[:-1])
    return "\n".join(block)


def _bullet_body(text: str, phrase: str) -> str:
    """Return the body of a Markdown block whose first line contains ``phrase``.

    A "block" can be:
      * a bullet (``* ...`` or ``- ...``) that wraps across indented
        continuation lines,
      * a heading (``### ...``) followed by paragraphs until the next
        heading of the same or higher level,
      * a row inside a fenced code block (for repository-layout-style
        lists).

    This is the robust scope helper so an assert on the G5 section, the
    Keyword+classifier bullet, or the redteam/ repo-layout row resolves
    to the right slice of text instead of letting a stray phrase
    elsewhere in the doc satisfy the assertion.
    """
    lines = text.splitlines()
    # 1. Bullet search.
    start = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if phrase in line and stripped.startswith(("* ", "- ", "*\t", "-\t")):
            start = index
            break
    if start is not None:
        bullet_prefix = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        block = [lines[start]]
        for line in lines[start + 1 :]:
            stripped = line.strip()
            if stripped == "":
                block.append(line)
                continue
            line_indent = len(line) - len(line.lstrip())
            if line_indent > len(bullet_prefix):
                block.append(line)
                continue
            if (line.startswith(("- ", "* ", "-\t", "*\t"))
                    and line_indent == len(bullet_prefix)):
                # New bullet at the same indent — stop.
                break
            # Any other top-level content ends the bullet.
            break
        return "\n".join(block).rstrip()

    # 2. Heading search.
    start = None
    heading_level = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if phrase in line and stripped.startswith("#"):
            start = index
            first_hash_run = len(line) - len(line.lstrip("#"))
            heading_level = first_hash_run
            break
    if start is not None and heading_level is not None:
        block = [lines[start]]
        for line in lines[start + 1 :]:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                first_hash_run = len(line) - len(line.lstrip("#"))
                if first_hash_run <= heading_level:
                    break
            block.append(line)
        # Strip any trailing blank lines.
        while block and block[-1].strip() == "":
            block.pop()
        return "\n".join(block)

    # 3. Code-fence row fallback. Many docs keep a repo-layout block as
    # indented/``-prefixed rows inside a triple-backtick block. Scan for
    # any line whose stripped content contains the phrase and is inside
    # a fence.
    in_fence = False
    fence_lines: list[str] = []
    matched = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            if phrase in line:
                matched = True
            fence_lines.append(line)
        elif matched:
            # Once we exit the fence after finding the phrase, return
            # the collected fence lines.
            return "\n".join(fence_lines).rstrip()
    if matched:
        return "\n".join(fence_lines).rstrip()

    # 4. Plain paragraph fallback.
    return _section(text, phrase)
