"""Shared prompt-fence neutralizer for both skill injection and memory recall.

Any string that enters a structured system-prompt fence (skill_reference, memory_block,
etc.) must be stripped of characters and patterns that could break the fence or forge
a trusted tag.  This module provides the single canonical implementation so that the
skill layer (Story B) and the memory layer (Story E) share identical sanitization
semantics.
"""
from __future__ import annotations

import re

# Strip markdown headers that appear at the start of a line (up to 3 leading spaces,
# then 1–6 # characters, then a space).  Structural guard — no English keywords.
_HEADER_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s.*$")

# After newlines are collapsed to a single space a header/directive marker (#{1,6}+space)
# can survive mid-prose; strip that token wherever it appears so an injected body can
# never reintroduce a heading/role marker.  Structural guard — no English keywords.
_INLINE_MARKER_RE = re.compile(r"#{1,6}\s")


def neutralize(text: str, *, cap: int | None = None) -> str:
    """Return *text* sanitized for use inside a structured prompt fence.

    Transformations applied in order:

    1. Strip ``<`` and ``>`` — prevents closing or forging XML-style fence tags.
    2. Strip ``"`` — prevents re-forming attribute syntax (e.g. ``trust="trusted"``)
       even after angle brackets are removed.
    3. Drop line-start markdown headers (``_HEADER_RE``).
    4. Collapse all whitespace (newlines, tabs, multiple spaces) to a single space.
    5. Drop any residual inline header/directive marker (``_INLINE_MARKER_RE``).
    6. If *cap* is given, truncate to ``text[:cap]``.

    Args:
        text: The raw untrusted string to sanitize.
        cap:  Optional maximum length.  ``None`` (default) means no truncation.
              Pass an explicit integer to enforce a hard cap.

    Returns:
        The sanitized string, at most *cap* characters long if *cap* is provided.
    """
    # 1+2. Strip angle brackets and double-quotes — fence-breakout prevention.
    text = text.replace("<", "").replace(">", "")
    text = text.replace('"', "")
    # 3. Drop line-start heading/role markers.
    text = _HEADER_RE.sub("", text)
    # 4. Collapse newlines/whitespace -> prose.
    text = " ".join(text.split())
    # 5. Drop any marker token that survived the collapse.
    text = _INLINE_MARKER_RE.sub("", text)
    # 6. Apply optional cap.
    if cap is not None:
        text = text[:cap]
    return text
