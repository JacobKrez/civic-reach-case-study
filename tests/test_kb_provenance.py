"""Knowledge-base provenance and freshness invariants."""

from datetime import date
from pathlib import Path


KB_DIR = Path(__file__).parents[1] / "knowledge_base"
REQUIRED = {
    "doc_id",
    "jurisdiction",
    "authority",
    "source_url",
    "retrieved",
    "effective",
    "supersedes",
}


def _front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8").lstrip()
    assert text.startswith("---\n"), f"{path}: missing YAML front matter"
    try:
        raw, _body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise AssertionError(f"{path}: unterminated YAML front matter") from exc
    fields = {}
    for line in raw.splitlines():
        if ":" not in line:
            raise AssertionError(f"{path}: malformed front matter line: {line!r}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def test_every_kb_document_has_complete_provenance():
    docs = sorted(KB_DIR.glob("*.md"))
    assert docs, "no knowledge-base documents found"
    seen = set()
    for path in docs:
        fields = _front_matter(path)
        assert set(fields) == REQUIRED, (
            f"{path}: provenance keys must be exactly {sorted(REQUIRED)}; "
            f"found {sorted(fields)}"
        )
        assert all(fields[key] for key in REQUIRED), f"{path}: empty provenance value"
        assert fields["jurisdiction"] in {"federal", "nc"}, (
            f"{path}: jurisdiction must be federal or nc"
        )
        assert fields["source_url"].startswith("https://"), f"{path}: source_url must be HTTPS"
        date.fromisoformat(fields["retrieved"])
        assert fields["doc_id"] not in seen, f"duplicate doc_id: {fields['doc_id']}"
        seen.add(fields["doc_id"])


def test_no_kb_document_is_out_of_date():
    today = date.today()
    for path in sorted(KB_DIR.glob("*.md")):
        effective = _front_matter(path)["effective"]
        try:
            start_raw, end_raw = effective.split("..", 1)
            start, end = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
        except (ValueError, TypeError) as exc:
            raise AssertionError(
                f"{path}: effective must be an ISO date range start..end"
            ) from exc
        assert start <= end, f"{path}: effective range starts after it ends"
        assert today <= end, (
            f"KB IS OUT OF DATE: {path} expired {end.isoformat()} "
            f"(today is {today.isoformat()})"
        )
