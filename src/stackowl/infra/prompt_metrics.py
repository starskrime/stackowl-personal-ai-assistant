"""Turn-scoped carrier for the assembled prompt's identity (D01.6).

Layering: ``providers`` cannot import ``pipeline`` — the dependency arrow points
the other way — but the single cost-recording site lives provider-side while the
prompt is built pipeline-side. So the two facts about the prompt that D01.6 needs
to measure travel through a ContextVar in ``infra``, which both may import.

Mirrors the idiom already used by ``pipeline/lesson_context.py`` and
``providers/escalation_signal.py``: per-async-context, so nothing leaks across
turns or between concurrent turns.

What this carries and why:

* ``prompt_hash`` — SHA-256[:16] of the exact system prompt sent. The D01.1
  stability invariant is ``COUNT(DISTINCT prompt_hash) per session_key == 1``.
  Before D01.1 it will equal the turn count, which IS the CONFLICT, measured.
* ``system_prompt_chars`` — size in characters, so prompt growth stays visible
  independently of tokenizer behaviour.

Deliberately NOT carried: the prompt text itself. Only a non-reversible digest
and a length ever reach ``cost_records`` (D01.6 invariant I5).
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar

_HASH_CHARS = 16

_prompt_hash: ContextVar[str] = ContextVar("prompt_hash", default="")
_prompt_chars: ContextVar[int] = ContextVar("system_prompt_chars", default=0)


def digest(system_prompt: str | None) -> str:
    """Stable short digest of a system prompt. Empty string for an absent prompt.

    Hashed over the exact string that will be sent, so a digest can never claim
    stability for something we did not send (D01.6 invariant I2).
    """
    if not system_prompt:
        return ""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:_HASH_CHARS]


def stamp(system_prompt: str | None) -> tuple[str, int]:
    """Record this turn's prompt identity. Returns ``(prompt_hash, chars)``.

    Called once per turn by the assemble step. Never raises: measurement must not
    become an outage (D01.6 invariant I1).
    """
    prompt_hash = digest(system_prompt)
    chars = len(system_prompt or "")
    _prompt_hash.set(prompt_hash)
    _prompt_chars.set(chars)
    return prompt_hash, chars


def current() -> tuple[str, int]:
    """Read this turn's prompt identity. ``("", 0)`` when nothing was stamped.

    ``("", 0)`` is the honest 'not measured' value — a turn that never reached
    assemble (a slash command, a health probe) genuinely has no prompt identity,
    and must not be confused with a prompt that hashed to something.
    """
    return _prompt_hash.get(), _prompt_chars.get()


def reset() -> None:
    """Clear the carrier. Test-support only."""
    _prompt_hash.set("")
    _prompt_chars.set(0)
