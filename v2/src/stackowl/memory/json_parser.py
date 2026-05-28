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
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # If there's leading prose, find the first { and slice.
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
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
