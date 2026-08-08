"""SkillStandardMigrator — bring the existing catalog up to the D10.2 standard.

MEASURED ON THE LIVE CATALOG, 2026-08-08, after consolidation: 168 skills, of
which 157 have a description over the 60-character cap. The validator refuses
non-conforming WRITES from now on; it cannot retroactively fix what was already
written, and 93% of the catalog predates it.

WHY AN LLM AND NOT A SCRIPT. Every other cleanup in this arc was deterministic —
consolidation collapses names, the curator moves states, neither touches
content. This one rewrites what a skill SAYS: a 197-character description has to
become a 60-character one without losing the retrieval signal, and a free-form
body has to become seven named sections in a fixed order. That is a
comprehension task; a regex would produce conforming skills that no longer
describe what they do, which is worse than non-conforming ones that do.

THE SAFETY MODEL, and it is stricter than consolidation's because this destroys
CONTENT rather than duplicates:

  * **Dry run by default**, like every irreversible pass here.
  * **The original body is archived before the rewrite**, outside the catalog,
    under a timestamp — so a bad rewrite costs a copy-back, not the skill.
  * **The rewrite is VALIDATED before it is written.** It goes through
    ``gated_skill_write``, which runs the standard, so an LLM that ignores the
    seven sections has its output refused and the original stays exactly as it
    was. This is the whole reason the validator was built before the migration
    rather than after.
  * **``standard_version`` is recorded only on success**, so a re-run retries
    the failures instead of skipping them. Recording conformance we did not
    verify would make the migrator skip precisely the skills it failed on — the
    one bug that would be invisible in its own report.
  * **Bounded per run.** ``limit`` exists because 157 LLM calls in one
    unattended pass is not something to discover the cost of afterwards.

WHAT IT DELIBERATELY DOES NOT DO: merge semantically-similar skills. The
consolidation pass collapses ``-N`` families by name; genuinely distinct skills
that happen to overlap (``structure-incident-evidence`` vs
``structure-evidence-brief``) are left alone. Deciding two skills are "really"
the same is a judgement with no undo, and it is not this pass's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from stackowl.infra.observability import log
from stackowl.memory.json_parser import parse_json_response
from stackowl.providers.base import Message
from stackowl.skills import standard
from stackowl.skills.authoring import SkillWriteRequest, gated_skill_write
from stackowl.skills.manifest import SkillManifest

if TYPE_CHECKING:
    from stackowl.skills.store import Skill, SkillIndexStore

__all__ = ["MigrationOutcome", "MigrationReport", "SkillStandardMigrator"]

#: A rewrite is one LLM call. Kept small by default so an operator discovers the
#: cost on a handful of skills rather than on the whole catalog.
DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class MigrationOutcome:
    """What happened to one skill — or, in a dry run, what would.

    ``ok`` is TRI-STATE via ``planned``: a preview entry is neither a success
    nor a failure, and reporting it as either is a lie the operator has to
    decode. The first dry run printed "failed 5" for five skills nothing had
    been attempted on.
    """

    name: str
    ok: bool
    reason: str
    old_description_len: int = 0
    new_description_len: int = 0
    #: True when this is a preview entry rather than an attempt.
    planned: bool = False

    def describe(self) -> str:
        mark = "·" if self.planned else ("✓" if self.ok else "✗")
        return f"  {mark} {self.name}: {self.reason}"


@dataclass
class MigrationReport:
    outcomes: list[MigrationOutcome] = field(default_factory=list)
    applied: bool = False
    archive_path: Path | None = None
    remaining: int = 0

    @property
    def migrated(self) -> int:
        return sum(1 for o in self.outcomes if o.ok and not o.planned)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok and not o.planned)

    @property
    def planned(self) -> int:
        return sum(1 for o in self.outcomes if o.planned)

    def summary(self) -> str:
        if not self.applied:
            return (
                f"would migrate {self.planned} of {self.remaining} skills below "
                f"v{standard.STANDARD_VERSION}"
            )
        return (
            f"migrated {self.migrated}, failed {self.failed}, "
            f"{self.remaining} still below v{standard.STANDARD_VERSION}"
        )


_SYSTEM = (
    "You rewrite an existing skill document so it conforms to a strict authoring "
    "standard, WITHOUT changing what the skill does.\n\n"
    "This is a reformatting task, not a redesign. Every procedural detail, command, "
    "threshold and caveat in the original must survive into the new structure. If the "
    "original does not say something, do not invent it — write what the section can "
    "honestly say from the material you were given, or state that the original did not "
    "specify it.\n\n"
)

_INSTRUCTION = (
    "Return ONLY a JSON object with keys \"description\", \"when_to_use\" and \"body\".\n"
    "- description: at most {max_desc} characters, ONE sentence, no trailing detail. "
    "It is a label, not an explanation.\n"
    "- when_to_use: 1-3 sentences. This carries the retrieval signal — say when someone "
    "should reach for this skill AND when they should not. Move the detail you had to "
    "cut from the description here.\n"
    "- body: markdown with EXACTLY these level-2 sections, in this order, each "
    "non-empty:\n{sections}\n"
    "Do not reference shell commands like grep/sed/awk/cat/ls/find by name; point at the "
    "real capability instead. Only reference tools by their registered names in backticks.\n"
)


class SkillStandardMigrator:
    """Rewrite pre-standard skills to conform. One entry point: :meth:`run`."""

    def __init__(
        self,
        store: SkillIndexStore,
        provider: object,
        *,
        archive_root: Path,
        model: str = "",
        consent_gate: object | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._archive_root = archive_root
        self._model = model
        self._consent_gate = consent_gate

    async def run(
        self,
        *,
        apply: bool = False,
        limit: int = DEFAULT_LIMIT,
        stamp: str,
    ) -> MigrationReport:
        # 1. ENTRY
        log.skills.debug(
            "[migrate] run: entry",
            extra={"_fields": {"apply": apply, "limit": limit}},
        )
        report = MigrationReport(applied=apply)
        candidates = await self._candidates()
        report.remaining = len(candidates)

        if not candidates:
            log.skills.info("[migrate] run: exit — catalog already at v%s",
                            standard.STANDARD_VERSION)
            return report

        batch = candidates[:limit]
        if not apply:
            report.outcomes = [
                MigrationOutcome(
                    name=sk.name, ok=False, planned=True,
                    reason=f"would rewrite (description is {len(sk.description)} chars)",
                    old_description_len=len(sk.description),
                )
                for sk in batch
            ]
            log.skills.info(
                "[migrate] run: exit (dry run) — %d of %d selected",
                len(batch), len(candidates),
            )
            return report

        report.archive_path = self._archive_root / stamp
        report.archive_path.mkdir(parents=True, exist_ok=True)

        for sk in batch:
            outcome = await self._migrate_one(sk, report.archive_path)
            report.outcomes.append(outcome)
            if outcome.ok:
                report.remaining -= 1

        # WARNING, not info: this rewrote skill CONTENT.
        log.skills.warning(
            "[migrate] run: exit — %s", report.summary(),
            extra={"_fields": {
                "migrated": report.migrated, "failed": report.failed,
                "remaining": report.remaining, "archive": str(report.archive_path),
            }},
        )
        return report

    async def _candidates(self) -> list[Skill]:
        """Skills below the current standard version, oldest-standard first.

        Archived skills are excluded: they are not offered, so paying an LLM
        call to reformat one buys nothing. Built-ins are excluded too — those
        are shipped files under version control, and rewriting them with an LLM
        would put generated content into the repository.
        """
        rows = await self._store.list_for_source("learned")
        out = [
            sk for sk in rows
            if sk.standard_version < standard.STANDARD_VERSION
            and sk.lifecycle_state != "archived"
        ]
        # Most-used first: if a run is cut short, the skills that actually get
        # retrieved are the ones that got fixed.
        out.sort(key=lambda s: (-s.n_executions, s.name))
        log.skills.debug(
            "[migrate] candidates",
            extra={"_fields": {"n": len(out), "version": standard.STANDARD_VERSION}},
        )
        return out

    async def _migrate_one(self, skill: Skill, archive: Path) -> MigrationOutcome:
        log.skills.debug(
            "[migrate] migrate_one: entry",
            extra={"_fields": {"name": skill.name, "desc_len": len(skill.description)}},
        )
        skill_dir = Path(skill.path)
        md = skill_dir / "SKILL.md"
        if not md.exists():
            return MigrationOutcome(skill.name, False, "SKILL.md missing on disk")

        # ARCHIVE FIRST. A rewrite we cannot back out of is not one we should
        # start; if the copy fails we do not touch the original at all.
        try:
            (archive / skill.name).mkdir(parents=True, exist_ok=True)
            (archive / skill.name / "SKILL.md").write_text(
                md.read_text(encoding="utf-8"), encoding="utf-8",
            )
        except Exception as exc:  # B5
            log.skills.error(
                "[migrate] migrate_one: archive failed — NOT rewriting",
                exc_info=exc, extra={"_fields": {"name": skill.name}},
            )
            return MigrationOutcome(skill.name, False, "could not archive the original")

        rewritten = await self._ask(skill)
        if rewritten is None:
            return MigrationOutcome(skill.name, False, "provider call failed or unparseable")

        description, when_to_use, body = rewritten

        try:
            manifest_dict = dict(skill.manifest_json)
        except Exception:  # noqa: BLE001 — a corrupt manifest_json is a data defect
            manifest_dict = {}
        manifest_dict.update({
            "name": skill.name,
            "description": description,
            "when_to_use": when_to_use,
            "version": _bump(str(manifest_dict.get("version") or skill.version)),
            "source": skill.source,
        })
        # Retired and index-owned keys never go back into a file.
        for dead in ("summary", "success_rate", "n_executions", "parent_traces",
                     "embedding_model"):
            manifest_dict.pop(dead, None)

        try:
            manifest = SkillManifest.model_validate(manifest_dict)
        except Exception as exc:  # B5
            log.skills.warning(
                "[migrate] migrate_one: rewritten frontmatter is not a valid manifest",
                exc_info=exc, extra={"_fields": {"name": skill.name}},
            )
            return MigrationOutcome(skill.name, False, "rewritten frontmatter invalid")

        request = SkillWriteRequest(
            target_dir=skill_dir,
            manifest=manifest,
            body=body,
            skill_md_text=_emit(manifest, body),
            consent_summary=(
                f"Migrate skill '{skill.name}' to authoring standard "
                f"v{standard.STANDARD_VERSION}"
            ),
            tool_name="skill_synthesizer",
        )
        # THE VALIDATION SEAM. gated_skill_write runs the standard, so a rewrite
        # that ignored the seven sections is refused here and the original file
        # is left exactly as it was. This is why the validator was built before
        # the migration and not after it.
        result = await gated_skill_write(
            request, store=self._store, consent_gate=self._consent_gate,  # type: ignore[arg-type]
        )

        # ONE RETRY, with the refusal fed back. The standard reports every
        # violation at once precisely so an author can fix them in a single
        # further attempt (R6Q22) — but the migrator was not taking that
        # attempt, so a rewrite that missed by ONE CHARACTER ("61 characters
        # exceeds the 60-character limit", seen live) cost a whole fresh call on
        # the next run to try the identical prompt again. Retrying here is
        # strictly cheaper than re-running, and it tells the model what was
        # wrong instead of hoping for a different sample.
        if not result.ok:
            retried = await self._ask(skill, correction=result.reason)
            if retried is not None:
                description, when_to_use, body = retried
                manifest_dict.update({
                    "description": description, "when_to_use": when_to_use,
                })
                try:
                    manifest = SkillManifest.model_validate(manifest_dict)
                except Exception as exc:  # B5
                    log.skills.warning(
                        "[migrate] migrate_one: corrected frontmatter still invalid",
                        exc_info=exc, extra={"_fields": {"name": skill.name}},
                    )
                else:
                    result = await gated_skill_write(
                        SkillWriteRequest(
                            target_dir=skill_dir, manifest=manifest, body=body,
                            skill_md_text=_emit(manifest, body),
                            consent_summary=request.consent_summary,
                            tool_name="skill_synthesizer",
                        ),
                        store=self._store,
                        consent_gate=self._consent_gate,  # type: ignore[arg-type]
                    )

        if not result.ok:
            log.skills.warning(
                "[migrate] migrate_one: rewrite rejected — original untouched",
                extra={"_fields": {"name": skill.name, "reason": result.reason[:300]}},
            )
            return MigrationOutcome(
                skill.name, False, f"rejected: {result.reason[:160]}",
                old_description_len=len(skill.description),
            )

        await self._store.set_standard_version(skill.skill_id, standard.STANDARD_VERSION)
        log.skills.info(
            "[migrate] migrate_one: exit — migrated",
            extra={"_fields": {
                "name": skill.name,
                "desc_before": len(skill.description),
                "desc_after": len(description),
            }},
        )
        return MigrationOutcome(
            skill.name, True, f"description {len(skill.description)} -> {len(description)} chars",
            old_description_len=len(skill.description),
            new_description_len=len(description),
        )

    async def _ask(
        self, skill: Skill, *, correction: str | None = None,
    ) -> tuple[str, str, str] | None:
        sections = "\n".join(
            f"    {i + 1}. ## {s}" for i, s in enumerate(standard.REQUIRED_SECTIONS)
        )
        instruction = _INSTRUCTION.format(
            max_desc=standard.MAX_DESCRIPTION_CHARS, sections=sections,
        )
        # Message OBJECTS, not dicts. The provider inspects attributes
        # (message.documents) to decide whether a turn carries content blocks,
        # so a plain dict raises AttributeError inside the provider — which the
        # first live batch demonstrated on all three skills.
        # On a retry, lead with what was actually wrong. A bare re-ask is just a
        # second sample from the same distribution.
        preamble = (
            f"Your previous attempt was REJECTED:\n{correction}\n\n"
            f"Fix exactly that and return the whole object again.\n\n"
            if correction else ""
        )
        messages = [
            Message(role="system", content=_SYSTEM + standard.describe_for_prompt()),
            Message(role="user", content=(
                f"{preamble}{instruction}\n"
                f"--- CURRENT SKILL: {skill.name} ---\n"
                f"description: {skill.description}\n"
                f"when_to_use: {skill.when_to_use}\n\n"
                f"{skill.body_text}\n"
                f"--- END ---"
            )),
        ]
        try:
            completion = await self._provider.complete(  # type: ignore[attr-defined]
                messages, model=self._model,
            )
        except Exception as exc:  # B5
            log.skills.warning(
                "[migrate] ask: provider call failed",
                exc_info=exc, extra={"_fields": {"name": skill.name}},
            )
            return None

        obj = parse_json_response(
            completion.content, required_keys=["description", "when_to_use", "body"],
        )
        if obj is None:
            log.skills.warning(
                "[migrate] ask: response unparseable",
                extra={"_fields": {
                    "name": skill.name, "preview": str(completion.content)[:200],
                }},
            )
            return None
        description = str(obj.get("description", "")).strip()
        when_to_use = str(obj.get("when_to_use", "")).strip()
        body = str(obj.get("body", "")).strip()
        if not (description and when_to_use and body):
            log.skills.warning(
                "[migrate] ask: response missing a required field",
                extra={"_fields": {"name": skill.name}},
            )
            return None
        return description, when_to_use, body


def _bump(version: str) -> str:
    """Patch-bump a semver string. Content changed, so the version must too."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return "0.1.1"
    return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"


def _emit(manifest: SkillManifest, body: str) -> str:
    """Render SKILL.md from frontmatter + body.

    Mirrors ``synthesizer._emit_skill_md`` rather than importing it: that one
    strips index-owned fields on the way out, and this path has already removed
    them from the manifest, so importing it would hide where the responsibility
    lies. Both must produce a file ``parse_skill_md`` accepts.
    """
    fm = manifest.model_dump(mode="json", exclude_none=True)
    for dead in ("success_rate", "n_executions", "parent_traces", "embedding_model"):
        fm.pop(dead, None)
    return f"---\n{yaml.safe_dump(fm, sort_keys=False).rstrip()}\n---\n\n{body.strip()}\n"
