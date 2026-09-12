"""Formatters and small queries supporting :class:`MemoryCommand`."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import CommandParseError
from stackowl.infra.observability import log
from stackowl.memory.models import StagedFact

if TYPE_CHECKING:
    from stackowl.audit.logger import AuditLogger
    from stackowl.memory.bridge import MemoryBridge

ExportFormat = Literal["json", "csv"]
_VALID_EXPORT_FORMATS: tuple[str, ...] = ("json", "csv")
_DEFAULT_EXPORT_FORMAT: ExportFormat = "json"


def format_budget(usage_bytes: int, ceiling_bytes: int) -> str:
    """Render the budget summary including pct used."""
    pct = (usage_bytes / ceiling_bytes * 100.0) if ceiling_bytes > 0 else 0.0
    return (
        "Memory budget:\n"
        f"  usage    {usage_bytes} bytes\n"
        f"  ceiling  {ceiling_bytes} bytes\n"
        f"  used     {pct:.2f}%"
    )


def _audit_memory_mutation(
    audit: AuditLogger,
    *,
    event_type: str,
    actor: str,
    target: str,
    details: dict[str, object],
) -> None:
    """Best-effort append of a memory-mutation audit row.

    Failures are logged and swallowed — losing an audit row must never abort
    the underlying memory mutation (which has already happened by call time).
    """
    try:
        audit.append(
            event_type=event_type, actor=actor, target=target, details=details,
        )
    except Exception as exc:  # B5 — never let audit failure break the mutation
        log.memory.error(
            "[memory] memory_helpers: audit append failed",
            exc_info=exc,
            extra={"_fields": {"event_type": event_type, "target": target}},
        )


# ``remember_fact`` stood here and went with FactPromoter in D08.2 seam 3 pass 4.
#
# It was the shared "stage then force-promote" chokepoint for both `/memory remember`
# and the `memory` tool. Neither routes through it any more: D08.1 retargeted both at
# curated memory, and a COMPLETE search — after a truncated one caused an earlier
# revert — confirmed no production caller remained. Its terminal step was
# ``promoter.force_promote()`` into ``committed_facts``, which has held 0 rows since
# migration 0112 and now has no writer at all.
#
# ``forget_fact`` below is NOT dead and stays: tools/knowledge/memory.py calls it, and
# it is the audited chokepoint for deletion.


async def forget_fact(
    bridge: MemoryBridge,
    fact_id: str,
    *,
    audit: AuditLogger | None = None,
    actor: str = "user:forget",
) -> None:
    """Delete a committed/staged fact and (optionally) audit — the shared
    forget-with-provenance chokepoint mirroring :func:`remember_fact`.

    BOTH the ``/memory forget`` slash path AND the ``memory`` tool route
    deletes through here, so a tool-driven delete cannot
    bypass the audit row a human delete would (eventually) leave. ``fact_id`` is
    the resolved, full fact id (the caller resolves any prefix first). When
    ``audit`` is supplied an append-only audit row is written; the slash path
    leaves it ``None`` to preserve existing behavior.
    """
    log.memory.debug(
        "[memory] memory_helpers.forget_fact: entry",
        extra={"_fields": {"fact_id": fact_id[:16]}},
    )
    await bridge.delete(fact_id)
    if audit is not None:
        _audit_memory_mutation(
            audit, event_type="memory.forget", actor=actor,
            target=fact_id, details={},
        )
    log.memory.info(
        "[memory] memory_helpers.forget_fact: exit",
        extra={"_fields": {"fact_id": fact_id}},
    )


def parse_export_args(args: str) -> tuple[ExportFormat, Path | None]:
    """Parse ``--format <json|csv>`` and ``--output <path>`` from args."""
    tokens = args.split()
    fmt: ExportFormat = _DEFAULT_EXPORT_FORMAT
    output_path: Path | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--format" and i + 1 < len(tokens):
            value = tokens[i + 1].lower()
            if value not in _VALID_EXPORT_FORMATS:
                log.memory.warning(
                    "[memory] memory_helpers.parse_export_args: invalid format",
                    extra={"_fields": {"value": value[:16]}},
                )
                raise CommandParseError(
                    "memory export",
                    f"invalid --format '{value}' (expected json or csv)",
                )
            fmt = value  # type: ignore[assignment]
            i += 2
            continue
        if tok == "--output" and i + 1 < len(tokens):
            output_path = Path(tokens[i + 1])
            i += 2
            continue
        i += 1
    return fmt, output_path


def _facts_to_rows(facts: list[StagedFact]) -> list[dict[str, str | float]]:
    """Render export rows (one dict per fact)."""
    rows: list[dict[str, str | float]] = []
    for f in facts:
        rows.append({
            "fact_id": f.fact_id,
            "content": f.content,
            "confidence": float(f.confidence),
            "committed_at": f.staged_at.isoformat(),
            "source_type": f.source_type,
        })
    return rows


def _render_export(rows: list[dict[str, str | float]], fmt: ExportFormat) -> str:
    if fmt == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["fact_id", "content", "confidence", "committed_at", "source_type"],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


async def do_export(
    facts: list[StagedFact],
    fmt: ExportFormat,
    output_path: Path | None,
) -> str:
    """Render ``facts`` and either write to ``output_path`` or return inline."""
    log.memory.debug(
        "[memory] memory_helpers.do_export: entry",
        extra={
            "_fields": {
                "count": len(facts),
                "format": fmt,
                "has_output": output_path is not None,
            }
        },
    )
    rows = _facts_to_rows(facts)
    rendered = _render_export(rows, fmt)
    if output_path is None:
        log.memory.debug(
            "[memory] memory_helpers.do_export: exit — inline",
            extra={"_fields": {"bytes": len(rendered)}},
        )
        return rendered
    # File I/O is real I/O — guard it.
    TestModeGuard.assert_not_test_mode("memory.export")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        # B5 — never silent on filesystem errors
        log.memory.error(
            "[memory] memory_helpers.do_export: write failed",
            exc_info=exc,
            extra={"_fields": {"path": str(output_path)}},
        )
        raise
    log.memory.info(
        "[memory] memory_helpers.do_export: exit — wrote file",
        extra={
            "_fields": {
                "path": str(output_path),
                "count": len(facts),
                "bytes": len(rendered),
            }
        },
    )
    return f"Exported {len(facts)} facts to {output_path}"


