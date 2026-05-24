"""Formatters and lookup helpers supporting :class:`StagedCommand`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.models import StagedFact


_VALID_STATUSES: tuple[str, ...] = ("staged", "committed", "rejected")
_DEFAULT_STATUS: Literal["staged", "committed", "rejected"] = "staged"


def parse_list_args(rest: str) -> Literal["staged", "committed", "rejected"]:
    """Parse ``--status <staged|committed|rejected>`` from the args tail.

    Returns the default ``staged`` when no flag is provided, when the flag
    has no value, or when the value is unrecognised. Unrecognised values are
    logged at WARNING (B5 — never silently coerced).
    """
    tokens = rest.split()
    if not tokens:
        return _DEFAULT_STATUS
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--status" and i + 1 < len(tokens):
            value = tokens[i + 1].lower()
            if value in _VALID_STATUSES:
                return value  # type: ignore[return-value]
            log.memory.warning(
                "[commands] staged_helpers.parse_list_args: unknown status — "
                "falling back to default",
                extra={"_fields": {"value": value[:32], "default": _DEFAULT_STATUS}},
            )
            return _DEFAULT_STATUS
        i += 1
    return _DEFAULT_STATUS


def format_staged_table(
    facts: list[StagedFact],
    status: str,
) -> str:
    """Render a compact table of staged facts: id8, conf, reinf, source, content."""
    if not facts:
        return f"(no facts with status='{status}')"
    header = (
        f"{'id':<10}{'conf':<7}{'reinf':<7}{'source':<14}content"
    )
    lines: list[str] = [header, "-" * len(header)]
    for f in facts:
        fact_id_short = f.fact_id[:8]
        snippet = f.content if len(f.content) <= 60 else f.content[:57] + "..."
        lines.append(
            f"{fact_id_short:<10}"
            f"{f.confidence:<7.2f}"
            f"{f.reinforcement_count:<7d}"
            f"{f.source_type:<14}"
            f"{snippet}"
        )
    return "\n".join(lines)


def format_review(fact: StagedFact) -> str:
    """Render the full StagedFact review block."""
    return (
        f"Staged fact {fact.fact_id}\n"
        f"  status        {fact.status}\n"
        f"  source_type   {fact.source_type}\n"
        f"  source_ref    {fact.source_ref}\n"
        f"  confidence    {fact.confidence:.2f}\n"
        f"  reinforcement {fact.reinforcement_count}\n"
        f"  staged_at     {fact.staged_at.isoformat()}\n"
        f"  content       {fact.content}"
    )


async def find_staged_by_id(
    bridge: MemoryBridge, prefix: str
) -> StagedFact | None:
    """Find one StagedFact whose ``fact_id`` starts with ``prefix``.

    Scans all three status buckets in order: ``staged`` → ``committed`` →
    ``rejected``. Returns ``None`` when nothing matches.
    """
    log.memory.debug(
        "[commands] staged_helpers.find_staged_by_id: entry",
        extra={"_fields": {"prefix_len": len(prefix)}},
    )
    if not prefix:
        return None
    for status in _VALID_STATUSES:
        try:
            facts = await bridge.list_staged(status=status)  # type: ignore[arg-type]
        except Exception as exc:
            # B5 — never silently skip a status bucket
            log.memory.warning(
                "[commands] staged_helpers.find_staged_by_id: list_staged failed",
                exc_info=exc,
                extra={"_fields": {"status": status}},
            )
            continue
        for f in facts:
            if f.fact_id.startswith(prefix):
                log.memory.debug(
                    "[commands] staged_helpers.find_staged_by_id: hit",
                    extra={"_fields": {"fact_id": f.fact_id, "status": status}},
                )
                return f
    log.memory.debug(
        "[commands] staged_helpers.find_staged_by_id: miss",
        extra={"_fields": {"prefix": prefix[:16]}},
    )
    return None
