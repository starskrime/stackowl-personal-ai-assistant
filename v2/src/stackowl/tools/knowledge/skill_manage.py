"""skill_manage — create/modify the agent's OWN skills (procedures).

This is the security-critical write tool of E4: the agent edits the very
skills that become its OWN future system prompt. A poisoned skill body
("always exfiltrate X / ignore safety on Y") would re-inject as trusted
first-party context on the next turn, closing a self-mutation loop with no
human in it. So EVERY content mutation routes through a HARD chokepoint:

    validate (frontmatter/name/category) → security_scan_gate (HARD, blocks on
    fail / fails closed on scanner crash) → record_skill_mutation (the single
    mutate-with-provenance path: before/after hash + restorable snapshot + audit
    row, so /skill diff + /skill restore cover agent-authored changes too) →
    reindex_after_change (so the new/edited skill is searchable).

A failing scan or validation BLOCKS the write with a structured error and NO
mutation. delete/enable/disable also route their persistence change through
record_skill_mutation (delete uses ``snapshot_when="before"`` so restore can
resurrect the dir; enable/disable use ``snapshot_when="none"``).

Severity (operator decision): ``consequential`` — every write is consent-gated.
The consent gate fails closed off-TTY, which doubles as the cron / non-interactive
protection (party #4): a poisoned skill written during an unattended run is the
worst case, and an off-TTY consent prompt simply denies.
``toolset_group="knowledge_write"`` (operator decision): isolated from the READ
``knowledge`` group so a read-only-knowledge owl never gets self-mutation hydrated.

Agent-authored skills live under the ``learned`` source
(``~/.stackowl/workspace/skills/learned/<name>/``) — distinct from human-authored
``user`` skills and shipped ``builtin`` skills (which the agent may not mutate).

Provenance / port-vs-build: HYBRID — the validators + static security scan are
ported (and re-expressed neutrally) into
:mod:`stackowl.tools.knowledge.skill_validation`; the mutate-with-provenance
plumbing reuses :mod:`stackowl.commands.skill_helpers`. See
``_bmad-output/research/tool-port-analysis.md`` (E4 ``skill_manage`` row).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.commands.skill_helpers import record_skill_mutation, reindex_after_change
from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import get_services
from stackowl.skills.loader import SkillLoader
from stackowl.skills.manifest import SkillManifest, SkillSource
from stackowl.skills.skill_md import parse_skill_md
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.knowledge.skill_validation import (
    security_scan_gate,
    validate_category,
    validate_content_size,
    validate_frontmatter,
    validate_skill_name,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.skills.store import SkillIndexStore

# Agent-authored skills land in the ``learned`` source — never builtin/user.
_SELF_SOURCE: SkillSource = "learned"
_ACTOR = "agent_self:skill_manage"

_VALID_ACTIONS: tuple[str, ...] = (
    "create", "edit", "patch", "delete", "enable", "disable",
)
# Actions that change a skill's CONTENT (validate + security-scan + reindex).
_CONTENT_ACTIONS: frozenset[str] = frozenset({"create", "edit", "patch"})

_SKILL_MD = "SKILL.md"


def _did_you_mean(action: str) -> str:
    """Render a structured 'did you mean' for an unknown action enum value."""
    valid = "|".join(_VALID_ACTIONS)
    suggestion = next((a for a in _VALID_ACTIONS if action and a[0] == action[0]), None)
    hint = f" Did you mean '{suggestion}'?" if suggestion else ""
    return f"Unknown action {action!r}. Valid actions: {valid}.{hint}"


class SkillManageTool(Tool):
    """Create/modify the agent's own skills: create/edit/patch/delete/enable/disable."""

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "Create or modify the agent's OWN skills (reusable procedures / "
            "how-tos that become future system-prompt context). "
            "action='create' writes a new skill (full SKILL.md); 'edit' replaces "
            "an existing skill's SKILL.md; 'patch' does a find/replace inside "
            "SKILL.md; 'delete' removes a skill; 'enable'/'disable' toggle it "
            "without deleting. Every write is security-scanned, audited, and "
            "restorable. "
            "LANE: authoring/changing procedural skills the agent can reuse. "
            "ANTI-LANE: do NOT use this to READ a skill (use skill_view) or to "
            "LIST skills (use skills_list); do NOT use it to remember a FACT "
            "(use memory). It edits skills, not capabilities/tools."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "create | edit | patch | delete | enable | disable",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name (lowercase, hyphen/underscore/dot).",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Full SKILL.md text (YAML frontmatter with name+description, "
                        "then a markdown body). Required for create/edit."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": "Optional single-segment category (no path separators).",
                },
                "find": {
                    "type": "string",
                    "description": "Exact substring to replace (action='patch').",
                },
                "replace": {
                    "type": "string",
                    "description": "Replacement text (action='patch').",
                },
            },
            "required": ["action"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            commit_coupling="transactional",
            toolset_group="knowledge_write",
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        action = str(kwargs.get("action", "")).strip().lower()
        # 1. ENTRY
        log.tool.info(
            "skill_manage.execute: entry",
            extra={"_fields": {"action": action}},
        )

        # Hard-validate the action enum — structured 'did you mean', never a
        # stack trace, never a silent default into a branch.
        if action not in _VALID_ACTIONS:
            return self._err(_did_you_mean(action), t0)

        # Self-healing: resolve the store once; a missing store surfaces as a
        # structured 'skills unavailable', never a raise.
        store = get_services().skill_store
        if store is None:
            return self._unavailable("store", "no skill store is configured", t0)

        try:
            # 2. DECISION — dispatch by validated action.
            if action == "create":
                return await self._create(store, kwargs, t0)
            if action == "edit":
                return await self._edit(store, kwargs, t0)
            if action == "patch":
                return await self._patch(store, kwargs, t0)
            if action == "delete":
                return await self._delete(store, kwargs, t0)
            return await self._set_enabled(store, kwargs, t0, enabled=action == "enable")
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "skill_manage.execute: action failed — degrading to structured error",
                exc_info=exc,
                extra={"_fields": {"action": action}},
            )
            return self._unavailable(action, f"{type(exc).__name__}: {exc}", t0)

    # ------------------------------------------------------------------ actions

    async def _create(
        self, store: SkillIndexStore, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        content = str(kwargs.get("content", ""))
        category = self._opt_str(kwargs.get("category"))

        gate = self._validate_content(name, content, category)
        if gate is not None:
            return self._err(gate, t0)

        target_dir = self._target_dir(name, category)
        if target_dir.exists():
            return self._err(
                f"Skill '{name}' already exists at {target_dir}. Use action='edit' "
                "to replace it, or pick a different name.",
                t0,
            )

        # HARD security gate (party #2): scan the WOULD-BE on-disk content before
        # any mutation. We stage it in a sibling temp dir, scan that, and only
        # mutate if the scan passes — so a dangerous body never touches the real tree.
        blocked = self._scan_or_block(content, name, target_dir)
        if blocked is not None:
            return self._err(blocked, t0)

        async def _mutate() -> None:
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / _SKILL_MD).write_text(self._normalized(content), encoding="utf-8")

        await record_skill_mutation(
            store,
            skill_name=name,
            source=_SELF_SOURCE,
            op="create",
            actor=_ACTOR,
            target_dir=target_dir,
            mutate=_mutate,
            snapshot_when="after",
            details={"category": category} if category else None,
        )
        reindex_note = await self._reindex(store)
        return self._ok(
            f"Created skill '{name}'." + reindex_note, t0,
            extra={"skill": name, "op": "create"},
        )

    async def _edit(
        self, store: SkillIndexStore, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        content = str(kwargs.get("content", ""))

        name_err = validate_skill_name(name)
        if name_err is not None:
            return self._err(name_err, t0)
        existing = await store.get(_SELF_SOURCE, name)
        if existing is None:
            return self._err(
                f"No agent-authored skill named '{name}' to edit. Use action='create' "
                "to make one. (Only 'learned' skills are editable by the agent.)",
                t0,
            )
        target_dir = Path(existing.path)

        gate = self._validate_content(name, content, None, check_name=False)
        if gate is not None:
            return self._err(gate, t0)
        blocked = self._scan_or_block(content, name, target_dir)
        if blocked is not None:
            return self._err(blocked, t0)

        async def _mutate() -> None:
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / _SKILL_MD).write_text(self._normalized(content), encoding="utf-8")

        await record_skill_mutation(
            store,
            skill_name=name,
            source=_SELF_SOURCE,
            op="update",
            actor=_ACTOR,
            target_dir=target_dir,
            mutate=_mutate,
            snapshot_when="after",
            skill_id=existing.skill_id,
        )
        reindex_note = await self._reindex(store)
        return self._ok(
            f"Edited skill '{name}'." + reindex_note, t0,
            extra={"skill": name, "op": "edit"},
        )

    async def _patch(
        self, store: SkillIndexStore, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        find = str(kwargs.get("find", ""))
        replace = str(kwargs.get("replace", ""))

        name_err = validate_skill_name(name)
        if name_err is not None:
            return self._err(name_err, t0)
        if not find:
            return self._err("action='patch' requires a non-empty 'find' string.", t0)
        existing = await store.get(_SELF_SOURCE, name)
        if existing is None:
            return self._err(
                f"No agent-authored skill named '{name}' to patch. "
                "(Only 'learned' skills are editable by the agent.)",
                t0,
            )
        target_dir = Path(existing.path)
        skill_md = target_dir / _SKILL_MD
        try:
            current = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            return self._err(f"Cannot read SKILL.md for '{name}': {exc}", t0)
        occurrences = current.count(find)
        if occurrences == 0:
            return self._err(
                f"Patch 'find' string not found in skill '{name}'. No change made.",
                t0,
            )
        if occurrences > 1:
            return self._err(
                f"Patch 'find' string is ambiguous in skill '{name}' "
                f"({occurrences} occurrences). Provide a longer, unique 'find'.",
                t0,
            )
        new_content = current.replace(find, replace)

        gate = self._validate_content(name, new_content, None, check_name=False)
        if gate is not None:
            return self._err(gate, t0)
        blocked = self._scan_or_block(new_content, name, target_dir)
        if blocked is not None:
            return self._err(blocked, t0)

        async def _mutate() -> None:
            skill_md.write_text(self._normalized(new_content), encoding="utf-8")

        await record_skill_mutation(
            store,
            skill_name=name,
            source=_SELF_SOURCE,
            op="update",
            actor=_ACTOR,
            target_dir=target_dir,
            mutate=_mutate,
            snapshot_when="after",
            skill_id=existing.skill_id,
            details={"patch": True},
        )
        reindex_note = await self._reindex(store)
        return self._ok(
            f"Patched skill '{name}'." + reindex_note, t0,
            extra={"skill": name, "op": "patch"},
        )

    async def _delete(
        self, store: SkillIndexStore, kwargs: dict[str, object], t0: float,
    ) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        name_err = validate_skill_name(name)
        if name_err is not None:
            return self._err(name_err, t0)
        existing = await store.get(_SELF_SOURCE, name)
        if existing is None:
            return self._err(
                f"No agent-authored skill named '{name}' to delete. "
                "(Only 'learned' skills are deletable by the agent.)",
                t0,
            )
        target_dir = Path(existing.path)
        skill_id = existing.skill_id

        # snapshot_when="before" so /skill restore can resurrect the deleted dir.
        async def _mutate() -> None:
            import shutil

            shutil.rmtree(target_dir, ignore_errors=True)
            await store.delete(skill_id)

        await record_skill_mutation(
            store,
            skill_name=name,
            source=_SELF_SOURCE,
            op="delete",
            actor=_ACTOR,
            target_dir=target_dir,
            mutate=_mutate,
            snapshot_when="before",
            skill_id=skill_id,
            details={"path": str(target_dir)},
        )
        reindex_note = await self._reindex(store)
        return self._ok(
            f"Deleted skill '{name}'." + reindex_note, t0,
            extra={"skill": name, "op": "delete"},
        )

    async def _set_enabled(
        self, store: SkillIndexStore, kwargs: dict[str, object], t0: float, *, enabled: bool,
    ) -> ToolResult:
        verb = "enable" if enabled else "disable"
        name = str(kwargs.get("name", "")).strip()
        name_err = validate_skill_name(name)
        if name_err is not None:
            return self._err(name_err, t0)
        existing = await store.get(_SELF_SOURCE, name)
        if existing is None:
            return self._err(
                f"No agent-authored skill named '{name}' to {verb}. "
                "(Only 'learned' skills are toggled by the agent.)",
                t0,
            )
        target_dir = Path(existing.path)
        skill_id = existing.skill_id

        # No content change — route the toggle through the provenance chokepoint
        # (snapshot_when="none") so the audit trail covers enable/disable too.
        async def _mutate() -> None:
            await store.set_enabled(skill_id, enabled=enabled)

        await record_skill_mutation(
            store,
            skill_name=name,
            source=_SELF_SOURCE,
            op=verb,
            actor=_ACTOR,
            target_dir=target_dir,
            mutate=_mutate,
            snapshot_when="none",
            skill_id=skill_id,
        )
        # No reindex: enable/disable is a flag toggle, not a tree change.
        return self._ok(
            f"{verb.capitalize()}d skill '{name}'.", t0,
            extra={"skill": name, "op": verb},
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _opt_str(raw: object) -> str | None:
        if raw is None:
            return None
        s = str(raw).strip()
        return s or None

    @staticmethod
    def _normalized(content: str) -> str:
        """Ensure the SKILL.md ends with exactly one trailing newline."""
        return content.rstrip("\n") + "\n"

    def _validate_content(
        self, name: str, content: str, category: str | None, *, check_name: bool = True,
    ) -> str | None:
        """Run the pure validators (name/category/frontmatter/size). Returns an
        error string to BLOCK with, or ``None`` if all pass."""
        if check_name:
            err = validate_skill_name(name)
            if err is not None:
                return err
        err = validate_category(category)
        if err is not None:
            return err
        err = validate_content_size(content)
        if err is not None:
            return err
        err = validate_frontmatter(content)
        if err is not None:
            return err
        # Frontmatter name must match the requested skill name so the index key
        # and the on-disk manifest never diverge.
        try:
            parsed = parse_skill_md(content)
        except Exception as exc:  # B5 — already structurally validated above
            return f"SKILL.md could not be parsed: {exc}"
        fm_name = parsed.frontmatter.get("name")
        if isinstance(fm_name, str) and fm_name.strip() and fm_name.strip() != name:
            return (
                f"Frontmatter name '{fm_name}' does not match the requested skill "
                f"name '{name}'. They must match."
            )
        # Full manifest validation (semver, source literal, etc.).
        fm = dict(parsed.frontmatter)
        fm["source"] = _SELF_SOURCE
        fm.setdefault("name", name)
        try:
            SkillManifest.model_validate(fm)
        except Exception as exc:  # B5 — structured block, not a raise
            return f"SKILL.md frontmatter is invalid: {exc}"
        return None

    def _scan_or_block(
        self, content: str, name: str, target_dir: Path,
    ) -> str | None:
        """Run the HARD security gate against the WOULD-BE content. Returns a
        block reason if the scan fails (or crashes — fails closed), else ``None``.

        The content is staged in a sibling temp dir named like the real skill so
        the scan sees the exact tree about to be written, WITHOUT touching the
        real skill dir.
        """
        import shutil
        import tempfile

        staging_parent = tempfile.mkdtemp(prefix="stackowl-skillscan-")
        try:
            staged = Path(staging_parent) / name
            staged.mkdir(parents=True, exist_ok=True)
            (staged / _SKILL_MD).write_text(self._normalized(content), encoding="utf-8")
            ok, reason = security_scan_gate(staged)
            if not ok:
                log.tool.warning(
                    "skill_manage.execute: security gate BLOCKED mutation",
                    extra={"_fields": {"skill": name, "target": str(target_dir)}},
                )
                return f"BLOCKED by security scan — no change made.\n{reason}"
            return None
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    @staticmethod
    def _target_dir(name: str, category: str | None) -> Path:
        root = StackowlHome.skills_dir() / _SELF_SOURCE
        return (root / category / name) if category else (root / name)

    async def _reindex(self, store: SkillIndexStore) -> str:
        """Reindex once per mutation so the change is searchable.

        Coalescing across calls within one turn needs turn-level state (E5
        follow-up) — NOT built here. On failure: retried once, then degrades to a
        structured "reindex pending" note surfaced IN THE RESULT (party #5) so the
        agent knows the skill saved but is not yet searchable.

        Returns a string fragment to append to the success message (empty when
        reindex succeeded).
        """
        services = get_services()
        loader = SkillLoader(
            tool_registry=services.tool_registry,
            owl_registry=services.owl_registry,
        )
        skills_root = StackowlHome.skills_dir()
        for attempt in (1, 2):
            try:
                await reindex_after_change(
                    loader, store, skills_root,
                    embedding_registry=services.embedding_registry,
                )
                return ""
            except Exception as exc:  # B5 — retry once, then degrade
                log.tool.warning(
                    "skill_manage.execute: reindex failed",
                    exc_info=exc,
                    extra={"_fields": {"attempt": attempt}},
                )
        return (
            " NOTE: the skill was saved and audited but is not yet searchable "
            "(reindex pending — retrieval will pick it up on next boot)."
        )

    @staticmethod
    def _ok(
        output: str, t0: float, *, extra: dict[str, object] | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "skill_manage.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "skill_manage.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(source: str, reason: str, t0: float) -> ToolResult:
        """Self-healing: a down/missing store degrades to a structured result —
        a FAILED ToolResult (so the model knows the write did not land), never a
        raise."""
        msg = f"skills unavailable ({source}): {reason}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "skill_manage.execute: store unavailable — structured degradation",
            extra={"_fields": {"source": source, "reason": reason, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
