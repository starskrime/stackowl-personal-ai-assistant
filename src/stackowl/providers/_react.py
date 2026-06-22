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

# NOTE: no trailing `$` anchor. A weak model often FLATTENS newlines (its `\n`
# arrive as a literal `n`), so `ACTION: skill_managen{...}` runs onto one physical
# line; a `$`-anchored regex missed it and the raw tool call leaked to the user.
# Without the anchor we match the name then validate it against the real tool set
# (see ``known``) so a flattened trailing char (`skill_managen`) is repaired to the
# real tool (`skill_manage`) and a genuinely bogus name is rejected (→ None).
_ACTION_RE = re.compile(r"(?im)^[ \t>*-]*ACTION:[ \t]*([a-z0-9_]+)")
# Capture the fenced payload generically: an explicit ```...``` block is the model's
# declared args. If present but not a JSON object, that's malformed -> rejected below
# (rather than silently falling through to a bare-object scan of trailing prose).
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _resolve_name(name: str, known: set[str] | None) -> str | None:
    """Validate/repair a parsed tool name against the known set.

    ``known is None`` → trust the parse (back-compat). Otherwise: exact match wins;
    else if a known name is a PREFIX of the parsed name, the surplus is a flattened
    newline/suffix the model ran on (``skill_managen`` → ``skill_manage``) — repair
    to the known name; else the name is bogus → None (never dispatch a fake tool)."""
    if known is None or name in known:
        return name
    candidates = [k for k in known if name.startswith(k)]
    if candidates:
        return max(candidates, key=len)  # longest valid prefix
    return None


def parse_react_action(
    text: str | None, known: set[str] | None = None
) -> tuple[str, dict[str, Any]] | None:
    """Return (tool_name, args) if the text contains a well-formed ACTION block, else None.

    ``known`` (optional) is the set of real tool names; when given, the parsed name
    is validated/repaired against it so a flattened-newline run-on or a hallucinated
    name never dispatches (returns None instead)."""
    if not text:
        return None
    m = _ACTION_RE.search(text)
    if not m:
        return None
    name = _resolve_name(m.group(1), known)
    if name is None:
        return None
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


def looks_like_tool_call(text: str | None) -> bool:
    """True when a FINAL answer is actually an (unparsed) tool call, not a reply.

    Used as the delivery guard: a model that tried to act but whose ACTION block we
    couldn't parse/dispatch must NEVER have that raw text shown to the user. Detects
    two shapes: an ``ACTION:`` line (reusing ``_ACTION_RE``), or a bare JSON object
    that IS the message (the model emitted just ``{"action": ...}`` with no prose)."""
    if not text:
        return False
    if _ACTION_RE.search(text):
        return True
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            # Unparseable but object-shaped and the whole message → still a leaked
            # tool-call attempt (e.g. flattened newlines broke the JSON).
            return True
        return isinstance(obj, dict)
    return False


class LoopGuard:
    """Detects a provider tool-loop spinning on identical (name, args) calls.

    observe(name, args) is called once per dispatched tool call. It returns a
    one-shot corrective directive string the moment a signature reaches
    ``warn_at`` repeats (so the model is told to change approach), and None
    otherwise. tripped() reports whether any signature has reached ``break_at``
    repeats, so the loop can stop early and deliver its best answer instead of
    burning the remaining iterations.
    """

    def __init__(self, warn_at: int = 3, break_at: int = 4) -> None:
        self._warn_at = warn_at
        self._break_at = break_at
        self._counts: dict[str, int] = {}
        self._warned: set[str] = set()

    def _signature(self, name: str, args: Any) -> str:
        # NOTE: default=str coerces any non-JSON-serializable arg to str(); two
        # genuinely different objects whose str() coincides collapse to one
        # signature (possible false-positive loop trip). Accepted as a heuristic —
        # typical JSON-shaped tool args are unaffected.
        try:
            return f"{name}|{json.dumps(args, sort_keys=True, default=str)}"
        except Exception:
            return f"{name}|{args!r}"

    def observe(self, name: str, args: Any) -> str | None:
        """Record a dispatched tool call. Returns a one-shot directive at warn_at repeats, else None."""
        try:
            sig = self._signature(name, args)
            self._counts[sig] = self._counts.get(sig, 0) + 1
            count = self._counts[sig]
            if count == self._warn_at and sig not in self._warned:
                self._warned.add(sig)
                from stackowl.providers._wrapup import LOOP_REPEAT_DIRECTIVE
                return LOOP_REPEAT_DIRECTIVE
        except Exception:
            pass
        return None

    def tripped(self) -> bool:
        """True when any signature has reached break_at repeats."""
        return any(c >= self._break_at for c in self._counts.values())


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
