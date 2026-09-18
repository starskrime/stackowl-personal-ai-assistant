"""AD-4's leak guard: scans every string in an event's ``attrs`` for a
secret-shaped substring before the row is built, and never fails the write.

Runs on EVERY ``record()`` call, not just ones a caller flags as sensitive --
the same reasoning as the log filter it reuses (``infra/observability.py``'s
FX-04): a secret can hide inside a value under an innocuous key, so the only
safe policy is to scan every string, every time.
"""

from __future__ import annotations

from typing import Any

from stackowl.infra.observability import redact_secret_shapes
from stackowl.journal.models import JournalAttrsBase


def scan_attrs(attrs: JournalAttrsBase) -> tuple[dict[str, Any], bool]:
    """Redact secret-shaped strings anywhere in ``attrs``, recursively.

    Returns the redacted, JSON-ready dict and whether anything was redacted.
    Never raises -- a leak guard that could fail the write it is protecting
    would trade a secret leak for a lost event, and AD-4 is explicit that a
    match here "redacts and records, don't fail the write".
    """
    redacted_any = False

    def _scan(value: Any) -> Any:
        nonlocal redacted_any
        if isinstance(value, str):
            new_value, changed = redact_secret_shapes(value)
            if changed:
                redacted_any = True
            return new_value
        if isinstance(value, dict):
            return {k: _scan(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_scan(v) for v in value]
        return value

    raw = attrs.model_dump(mode="json")
    scanned = {k: _scan(v) for k, v in raw.items()}
    return scanned, redacted_any
