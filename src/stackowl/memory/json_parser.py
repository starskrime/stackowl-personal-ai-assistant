"""Shared JSON-from-LLM-output parser.

Several LLM call sites (critic scorer, reflection writer, future fact
extractor refinements) all need to parse a JSON object out of model
output that may be wrapped in ```json fences or surrounded by prose.
This helper centralises the tolerance logic so each consumer just
declares "I expect these keys" and gets a validated dict or None.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from stackowl.infra.observability import log


def _first_json_object(text: str) -> str | None:
    """The first balanced ``{...}`` in ``text``, or None if there isn't one.

    Brace-counting rather than a regex, because JSON nests and a regex cannot
    match balanced delimiters. String-aware: a ``{`` or ``}`` inside a JSON
    string literal must not move the depth, or any object containing a brace in
    a value (a code snippet, a template, a regex — all things this platform's
    models genuinely emit) would be truncated at the wrong place.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # Unbalanced (truncated output). Return what we have and let json.loads
    # produce the real error — it reports the position, which is more useful
    # than a bare None from here.
    return text[start:]


def parse_json_response(
    raw: str, required_keys: Iterable[str] | None = None,
) -> dict[str, object] | None:
    """Pull a JSON object out of an LLM response.

    Tolerates ```json fenced blocks, leading prose, and trailing prose.
    Returns None if no valid JSON object is found, or if ``required_keys``
    is supplied and any key is missing.
    """
    # 1. ENTRY
    log.memory.debug(
        "[json_parser] parse: entry",
        extra={"_fields": {
            "raw_len": len(raw),
            "required_keys": list(required_keys) if required_keys else [],
        }},
    )
    text = raw.strip()
    # Strip common ```json fences.
    #
    # ADR-19 — the fence is stripped WHEREVER it appears, not only at position 0.
    # The old check was `text.startswith("```")`, so the extremely common
    # "Here is the result:\n```json\n{...}\n```" kept its fence, and the brace
    # slice below then handed a trailing "```" to json.loads. A model adding one
    # polite sentence silently lost its whole response.
    fence = text.find("```")
    if fence >= 0:
        lines = text[fence:].splitlines()
        if lines:
            lines = lines[1:]                     # drop the opening ```json line
        for i, line in enumerate(lines):          # drop from the closing fence on
            if line.startswith("```"):
                lines = lines[:i]
                break
        text = "\n".join(lines).strip()
    # Slice out the first BALANCED object rather than everything from the first
    # brace to EOF. The docstring has always promised trailing-prose tolerance;
    # the code only ever implemented the leading half, so "…}\nHope that helps."
    # failed to parse. Measured: 2 of 5 realistic model shapes were rejected.
    extracted = _first_json_object(text)
    if extracted is not None:
        text = extracted
    # 3. STEP — actual JSON parse
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        log.memory.debug(
            "[json_parser] parse: exit — json.JSONDecodeError",
            extra={"_fields": {"msg": str(exc), "preview": text[:120]}},
        )
        return None
    # 2. DECISION — must be a dict
    if not isinstance(obj, dict):
        log.memory.debug(
            "[json_parser] parse: exit — not a JSON object",
            extra={"_fields": {"type": type(obj).__name__}},
        )
        return None
    # 2. DECISION — required keys present
    if required_keys is not None:
        missing = [k for k in required_keys if k not in obj]
        if missing:
            log.memory.debug(
                "[json_parser] parse: exit — missing required keys",
                extra={"_fields": {"missing": missing}},
            )
            return None
    # 4. EXIT
    log.memory.debug(
        "[json_parser] parse: exit — ok",
        extra={"_fields": {"keys": list(obj.keys())}},
    )
    return obj
