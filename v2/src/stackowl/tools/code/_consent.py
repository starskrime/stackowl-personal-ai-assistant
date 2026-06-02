"""Consent-digest helper for ExecuteCodeTool (extracted to keep the tool ≤300, B2).

The consent prompt is the ONE trusted place the to-be-run code is shown to the
user (GAP-A). This bounds that digest to the first N lines / chars with an honest
elision note so a huge program never floods the prompt; the gate truncates again
defensively. Pure + side-effect-free.
"""

from __future__ import annotations

__all__ = ["bounded_code", "CONSENT_CODE_MAX_LINES", "CONSENT_CODE_MAX_CHARS"]

# Bounded code digest shown in the consent prompt: enough for the user to judge
# what runs, never an unbounded dump.
CONSENT_CODE_MAX_LINES = 40
CONSENT_CODE_MAX_CHARS = 1000


def bounded_code(code: str) -> str:
    """Bound the code to the first N lines / chars with an honest elision note."""
    lines = code.splitlines()
    clipped = False
    if len(lines) > CONSENT_CODE_MAX_LINES:
        lines = lines[:CONSENT_CODE_MAX_LINES]
        clipped = True
    digest = "\n".join(lines)
    if len(digest) > CONSENT_CODE_MAX_CHARS:
        digest = digest[:CONSENT_CODE_MAX_CHARS]
        clipped = True
    if clipped:
        digest = f"{digest}\n…[code truncated for display]"
    return digest
