"""Formatters and small queries supporting :class:`MemoryCommand`."""

from __future__ import annotations

import csv
import io
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import CommandParseError, DuplicateFactError
from stackowl.infra.observability import log
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_helpers import unpack_embedding

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.memory.models import MemoryRecord


ExportFormat = Literal["json", "csv"]
_VALID_EXPORT_FORMATS: tuple[str, ...] = ("json", "csv")
_DEFAULT_EXPORT_FORMAT: ExportFormat = "json"


_STATS_SQL = {
    "staged": "SELECT COUNT(*) AS cnt FROM staged_facts WHERE status = 'staged'",
    "committed": "SELECT COUNT(*) AS cnt FROM committed_facts",
    "rejected": (
        "SELECT COUNT(*) AS cnt FROM staged_facts WHERE status = 'rejected'"
    ),
    "bytes": "SELECT COALESCE(SUM(length(content)), 0) AS s FROM committed_facts",
}


async def collect_stats(db: DbPool) -> dict[str, int]:
    """Aggregate counters for the :meth:`MemoryCommand._stats` subcommand."""
    out: dict[str, int] = {}
    for key, sql in _STATS_SQL.items():
        rows = await db.fetch_all(sql)
        if not rows:
            out[key] = 0
            continue
        first = rows[0]
        if "cnt" in first:
            out[key] = int(first["cnt"])
        else:
            out[key] = int(first["s"])
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS cnt FROM audit_log WHERE event_type = 'prune'"
    )
    out["pruned"] = int(rows[0]["cnt"]) if rows else 0
    return out


def format_stats(stats: dict[str, int]) -> str:
    """Render a small ASCII table of memory counters."""
    rows = [
        ("staged", stats.get("staged", 0)),
        ("committed", stats.get("committed", 0)),
        ("rejected", stats.get("rejected", 0)),
        ("pruned", stats.get("pruned", 0)),
        ("bytes", stats.get("bytes", 0)),
    ]
    width = max(len(label) for label, _ in rows)
    lines = ["Memory statistics:"]
    lines.extend(f"  {label:<{width}}  {value}" for label, value in rows)
    return "\n".join(lines)


def format_search_hits(hits: list[MemoryRecord]) -> str:
    """Render a short list of recall hits for slash-command output."""
    if not hits:
        return "(no matches)"
    lines = [f"{len(hits)} match(es):"]
    for h in hits:
        snippet = h.content if len(h.content) <= 120 else h.content[:117] + "..."
        lines.append(f"  - [{h.fact_id}] {snippet}")
    return "\n".join(lines)


def format_budget(usage_bytes: int, ceiling_bytes: int) -> str:
    """Render the budget summary including pct used."""
    pct = (usage_bytes / ceiling_bytes * 100.0) if ceiling_bytes > 0 else 0.0
    return (
        "Memory budget:\n"
        f"  usage    {usage_bytes} bytes\n"
        f"  ceiling  {ceiling_bytes} bytes\n"
        f"  used     {pct:.2f}%"
    )


async def remember_fact(
    bridge: MemoryBridge,
    promoter: FactPromoter,
    text: str,
) -> str:
    """Stage ``text`` as a user-explicit fact and force-promote it.

    Returns the new ``fact_id``. Built with confidence=1.0 and
    reinforcement_count=3 so it immediately meets the standard promotion
    gates even if force-promote is later removed from the pipeline.
    """
    log.memory.debug(
        "[memory] memory_helpers.remember_fact: entry",
        extra={"_fields": {"text_len": len(text)}},
    )
    fact = StagedFact(
        fact_id=str(uuid.uuid4()),
        content=text,
        source_type="manual",
        source_ref="user_explicit",
        confidence=1.0,
        reinforcement_count=3,
    )
    try:
        await bridge.stage(fact)
    except DuplicateFactError as exc:
        # B5 — log and re-raise so the caller can surface the failure
        log.memory.warning(
            "[memory] memory_helpers.remember_fact: duplicate fact_id collision",
            exc_info=exc,
            extra={"_fields": {"fact_id": fact.fact_id}},
        )
        raise
    await promoter.force_promote(fact.fact_id)
    log.memory.info(
        "[memory] memory_helpers.remember_fact: exit",
        extra={"_fields": {"fact_id": fact.fact_id}},
    )
    return fact.fact_id


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


async def fetch_all_committed_for_reindex(
    db: DbPool,
) -> list[tuple[str, list[float], dict[str, str]]]:
    """Return ``(fact_id, embedding, metadata)`` for every committed fact."""
    rows = await db.fetch_all(
        """SELECT fact_id, content, embedding, source_type, source_ref
           FROM committed_facts"""
    )
    out: list[tuple[str, list[float], dict[str, str]]] = []
    for row in rows:
        embedding = unpack_embedding(row["embedding"])
        if not embedding:
            log.memory.warning(
                "[memory] memory_helpers.fetch_all_committed_for_reindex: skip",
                extra={"_fields": {"fact_id": row["fact_id"], "reason": "no_embedding"}},
            )
            continue
        metadata = {
            "source_type": row["source_type"],
            "source_ref": row["source_ref"],
            "content": row["content"],
        }
        out.append((row["fact_id"], embedding, metadata))
    return out
