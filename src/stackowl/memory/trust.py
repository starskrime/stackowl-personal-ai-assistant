"""Single source of truth: memory content trust tier, assigned MECHANICALLY from the source
channel (never the owl's judgment). Default 'untrusted' (fail-safe). 'manual' (the human /remember
+ telegram-confirm path) is the only producer of 'trusted'; agent-callable surfaces hardcode
'agent_self' (-> self) and are structurally incapable of submitting 'manual'. Story E."""
from __future__ import annotations

from typing import Literal

Trust = Literal["trusted", "self", "untrusted"]
SAFE_DEFAULT: Trust = "untrusted"

_SOURCE_TRUST: dict[str, Trust] = {
    "manual": "trusted",
    "agent_self": "self",
    "parliament": "self",
    "conversation": "self",
    "conversation_fact": "self",
    "webpage": "untrusted",
    "screenshot": "untrusted",
}


def trust_for_source(source_type: str) -> Trust:
    """Map a source_type to its trust tier. Unknown -> SAFE_DEFAULT (untrusted, fail-safe)."""
    return _SOURCE_TRUST.get(source_type, SAFE_DEFAULT)


#: The one place a stored item is turned into text a model will read.
#:
#: ESC-6 (2026-08-14) — this rule used to live inside ``SqliteMemoryBridge.retrieve()``,
#: which MEASURED as the path nothing reaches: it hydrates from ``committed_facts`` (0
#: rows since migration 0112, no writers) and its output stopped entering the system
#: prompt with D01.1. Meanwhile the paths that DO reach the model — the ``memory``
#: tool's get/search/forget renders — had no fence at all, so ``webpage`` rows in
#: ``staged_facts`` could be surfaced raw via an id-prefix lookup. The invariant was
#: kept and MOVED here rather than dropped or duplicated.
_UNTRUSTED_SOURCE_CAP = 40


def render_at_trust(
    content: str,
    *,
    source_type: str,
    trust: Trust | None,
    cap: int | None = None,
) -> str:
    """Render one stored item for a model, framed by its trust tier.

    SECURITY-CRITICAL, and the reason it is one function: the fence is only a
    boundary if the thing inside it cannot close it. Two invariants hold together.

    1. **Every tier is neutralized, unconditionally.** Trust decides the FRAMING,
       never whether sanitisation happens — so a mis-tagged or forged-tier item is
       still incapable of breaking out. Dropping this for the trusted tier would
       make a single bad stamp sufficient to inject.
    2. **The fence attributes come from arguments, never from content.** ``trust``
       is the caller's value and ``source_type`` is sanitised before it reaches an
       attribute position, because a DB column is not a promise.

    ``trust=None`` — an absent or unrecognised stamp — fails safe to fenced. That
    matches :func:`trust_for_source`'s ``SAFE_DEFAULT``: unknown provenance is
    untrusted provenance, never bare confirmed fact.
    """
    from stackowl.infra.observability import log
    from stackowl.infra.prompt_safety import neutralize

    # 1. ENTRY
    log.memory.debug(
        "[memory] render_at_trust: entry",
        extra={"_fields": {"source_type": source_type, "trust": trust, "len": len(content)}},
    )
    safe = neutralize(content, cap=cap)
    # 2. DECISION — tier chooses the frame; sanitisation already happened above.
    if trust == "trusted":
        out = safe
    elif trust == "self":
        out = f"{safe} (working hypothesis; revise if new evidence contradicts)"
    else:
        # 3. STEP — untrusted, or an unknown/absent stamp: fence it.
        safe_source = neutralize(source_type, cap=_UNTRUSTED_SOURCE_CAP)
        out = (
            f'<memory_reference trust="untrusted" source="{safe_source}">'
            f"{safe}</memory_reference>"
        )
    # 4. EXIT
    log.memory.debug(
        "[memory] render_at_trust: exit",
        extra={"_fields": {"fenced": trust not in ("trusted", "self"), "len": len(out)}},
    )
    return out
