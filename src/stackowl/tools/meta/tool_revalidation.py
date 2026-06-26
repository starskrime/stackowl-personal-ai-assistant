"""Back-catalog re-validation of learned tools (verification arc, Branch 4b).

A learned tool is a declarative spec under ``learned_tools_dir()`` that the boot
loader (:class:`LearnedToolLoader`) re-registers on every start — a permanent
capability. The self-learning loop minted these, but before the verification arc it
had no trustworthy success signal: a tool that CLAIMED success while producing
nothing (the ``instagram_media_extractor`` class — argv ``--simulate --no-download``)
was reinforced as healthy.

Now that B4b stamps a general ``failure_class`` on a turn whose only effect was
unverified, a tool's ``task_outcomes`` history carries the truth. This one-time pass
re-checks each learned tool against that history and quarantines the ones that, with
enough evidence to judge, NEVER produced a trustworthy success — so the loader stops
re-registering a known-useless capability.

Quarantine, not delete: the suspect spec is MOVED to a sibling directory so the
decision is reversible and auditable. General/vendor-neutral — the predicate is the
tool's own win record, never a name or site.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.paths import StackowlHome

#: A learned tool needs at least this many recorded attempts before its win record
#: can condemn it — too little history is "no opinion", never an eviction.
_DEFAULT_MIN_EVIDENCE = 3


@dataclass(frozen=True)
class RevalidationReport:
    """Outcome of one re-validation pass."""

    #: Tools with ≥ min_evidence attempts and ZERO trustworthy successes (candidates).
    suspects: list[str] = field(default_factory=list)
    #: Suspects actually moved to quarantine (== suspects unless ``dry_run``).
    evicted: list[str] = field(default_factory=list)
    #: Tools with at least one trustworthy success — kept.
    kept: list[str] = field(default_factory=list)
    #: Tools with fewer than min_evidence attempts — left alone (no opinion).
    insufficient_evidence: list[str] = field(default_factory=list)
    #: Learned tools with no recorded outcomes at all — left alone.
    no_history: list[str] = field(default_factory=list)


def _learned_tool_names(learned_dir: Path) -> dict[str, Path]:
    """Map each learned tool's NAME (the spec ``name`` field, falling back to the file
    stem) to its spec file. A file that cannot be read/parsed is keyed by stem so it
    is still visible to the pass."""
    import json

    names: dict[str, Path] = {}
    for spec_file in sorted(learned_dir.glob("*.json")):
        name = spec_file.stem
        try:
            raw = json.loads(spec_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                name = raw["name"]
        except (OSError, json.JSONDecodeError) as exc:
            log.tool.warning(
                "[tools] revalidate: could not parse spec — keying by file stem",
                extra={"_fields": {"file": spec_file.name, "error": str(exc)}},
            )
        names[name] = spec_file
    return names


async def revalidate_learned_tools(
    db: DbPool,
    learned_dir: Path | None = None,
    *,
    min_evidence: int = _DEFAULT_MIN_EVIDENCE,
    dry_run: bool = False,
    quarantine_dir: Path | None = None,
) -> RevalidationReport:
    """Re-validate every learned tool against its trustworthy-success history.

    A learned tool is EVICTED (moved to ``quarantine_dir``) when it has at least
    ``min_evidence`` recorded attempts and not a single trustworthy success
    (``success=1 AND failure_class IS NULL``). ``dry_run`` reports without moving
    anything. Never raises on a single bad file — the pass is best-effort.
    """
    learned_dir = learned_dir or StackowlHome.learned_tools_dir()
    quarantine_dir = quarantine_dir or (learned_dir.parent / "learned_tools_evicted")
    log.tool.info(
        "[tools] revalidate: entry",
        extra={"_fields": {
            "dir": str(learned_dir), "min_evidence": min_evidence, "dry_run": dry_run,
        }},
    )

    report = RevalidationReport()
    if not learned_dir.is_dir():
        log.tool.info("[tools] revalidate: no learned-tools dir — nothing to do")
        return report

    specs = _learned_tool_names(learned_dir)
    if not specs:
        log.tool.info("[tools] revalidate: no learned tools on disk")
        return report

    store = TaskOutcomeStore(db)
    counts = await store.tool_outcome_trust_counts()

    for name, spec_file in specs.items():
        trustworthy, total = counts.get(name, (0, 0))
        if total == 0:
            report.no_history.append(name)
            continue
        if total < min_evidence:
            report.insufficient_evidence.append(name)
            continue
        if trustworthy > 0:
            report.kept.append(name)
            continue
        # ≥ min_evidence attempts, zero trustworthy successes → suspect.
        report.suspects.append(name)
        log.tool.warning(
            "[tools] revalidate: learned tool has no trustworthy successes",
            extra={"_fields": {"tool": name, "attempts": total, "trustworthy": 0}},
        )
        if dry_run:
            continue
        try:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(spec_file), str(quarantine_dir / spec_file.name))
            report.evicted.append(name)
            log.tool.info(
                "[tools] revalidate: quarantined learned tool",
                extra={"_fields": {"tool": name, "to": str(quarantine_dir)}},
            )
        except OSError as exc:  # best-effort — one unmovable file never aborts the pass
            log.tool.error(
                "[tools] revalidate: could not quarantine spec — leaving in place",
                exc_info=exc,
                extra={"_fields": {"tool": name, "file": spec_file.name}},
            )

    log.tool.info(
        "[tools] revalidate: exit",
        extra={"_fields": {
            "suspects": len(report.suspects), "evicted": len(report.evicted),
            "kept": len(report.kept),
            "insufficient_evidence": len(report.insufficient_evidence),
            "no_history": len(report.no_history),
        }},
    )
    return report
