"""ReAct text-protocol parser — lets models without native tool-calling act via text.

A model emits:
    ACTION: <tool_name>
    ```json
    {"arg": "value"}
    ```
We parse (tool_name, args) and dispatch through the normal tool chokepoint. Structural
ASCII tokens only (language-neutral); malformed input returns None (never raises).
"""

from __future__ import annotations

import json
import re
from typing import Any

_ACTION_RE = re.compile(r"(?im)^[ \t>*-]*ACTION:[ \t]*([a-z0-9_]+)[ \t]*$")
# Capture the fenced payload generically: an explicit ```...``` block is the model's
# declared args. If present but not a JSON object, that's malformed -> rejected below
# (rather than silently falling through to a bare-object scan of trailing prose).
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_react_action(text: str | None) -> tuple[str, dict[str, Any]] | None:
    """Return (tool_name, args) if the text contains a well-formed ACTION block, else None."""
    if not text:
        return None
    m = _ACTION_RE.search(text)
    if not m:
        return None
    name = m.group(1)
    rest = text[m.end():]
    # Prefer a fenced ```json {...} ``` block; fall back to the first balanced {...}.
    fence = _FENCE_RE.search(rest)
    raw = fence.group(1) if fence else _first_balanced_object(rest)
    if raw is None:
        # ACTION with no args block -> treat as zero-arg call.
        return (name, {})
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(args, dict):
        return None
    return (name, args)


def _first_balanced_object(s: str) -> str | None:
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None
