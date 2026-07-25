"""Best-effort, write-time-redacted structured turn telemetry."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .guardrails.pii import redact_for_logging

# Defense in depth for nested model/tool/state values.  The canonical redactor
# handles prose; these patterns ensure common identifiers cannot survive in any
# serialized field.
_PII = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b\d{1,6}\s+[A-Za-z0-9.' -]+\s(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct)\b", re.I),
]


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        value = redact_for_logging(value)
        for pattern in _PII:
            value = pattern.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {str(k): _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def emit_turn(event: dict[str, Any]) -> None:
    """Append one event. Telemetry must never affect a user turn."""
    try:
        level = os.getenv("CIVICREACH_LOG_LEVEL", "INFO").upper()
        if level in {"OFF", "NONE", "DISABLED", "0"}:
            return
        directory = Path(os.getenv("CIVICREACH_LOG_DIR", "logs"))
        directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        payload = {"timestamp": now.isoformat(), **event}
        line = json.dumps(_redact(payload), ensure_ascii=False, default=str)
        with (directory / f"turns-{now.date().isoformat()}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def replay(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSONL trace in turn order for deterministic debugging."""
    events: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                events.append(json.loads(line))
    return sorted(events, key=lambda e: (str(e.get("session_id", "")), int(e.get("turn_index", 0))))


__all__ = ["emit_turn", "replay"]
