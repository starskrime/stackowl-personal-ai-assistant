"""``/skill`` slash command — user CRUD over the unified Skill workspace.

Subcommands:

* ``/skill list [--source <s>]``                  — list skills (optionally filtered)
* ``/skill show <name>``                          — print SKILL.md frontmatter + body
* ``/skill add <local-path>``                     — install from a local directory
* ``/skill add --url <url>``                      — install from git URL or archive URL
* ``/skill rm <name> [YES]``                      — delete a non-builtin skill
* ``/skill edit <name>``                          — print path to SKILL.md (open it yourself)
* ``/skill diff <name>``                          — show recent audit entries for the skill
* ``/skill enable <name>`` / ``/skill disable <name>`` — toggle without deleting
* ``/skill reload``                               — rescan disk + refresh SQLite index

Sub-phase 3b of Learning Commit 3 (see plan gleaming-finding-puppy.md).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import Action, CommandResponse
from stackowl.commands.skill_helpers import (
    SkillInstallError,
    hash_dir,
    install_from_archive_url,
    install_from_git_url,
    install_from_local_path,
    record_skill_mutation,
    reindex_after_change,
    restore_snapshot,
)
from stackowl.infra.observability import log
from stackowl.skills.loader import SkillLoader
from stackowl.skills.manifest import SkillSource
from stackowl.skills.store import Skill, SkillIndexStore

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.pipeline.state import PipelineState


_CONFIRMATION = "YES"
_VALID_SOURCES: tuple[SkillSource, ...] = ("builtin", "installed", "user", "learned")

_SKILL_META = CommandMeta(
    grammar="verb",
    group="Memory & Knowledge",
    subcommands=(
        SubCommand(
            name="list",
            summary="List installed skills",
            description=(
                "You see every skill across all sources with its enable state and "
                "success rate. Filter with --source to narrow the view."
            ),
            args=(
                Arg(
                    name="--source",
                    required=False,
                    summary="filter by source",
                    choices=("builtin", "installed", "user", "learned"),
                ),
            ),
            examples=(
                Example(invocation="/skill list"),
                Example(invocation="/skill list --source user"),
            ),
        ),
        SubCommand(
            name="show",
            summary="Print a skill's frontmatter and body",
            description=(
                "You read a skill's full SKILL.md — metadata, stats, and the "
                "instruction body — to understand what it does."
            ),
            args=(Arg(name="name", summary="skill name"),),
            examples=(Example(invocation="/skill show research"),),
        ),
        SubCommand(
            name="add",
            summary="Install from a path or URL",
            description=(
                "You install a skill from a local directory, a git URL, or an archive "
                "URL. The workspace re-indexes so the skill is usable immediately."
            ),
            args=(
                Arg(name="local-path", required=False, summary="local directory to install"),
                Arg(name="--url", required=False, summary="git or archive URL"),
            ),
            examples=(
                Example(invocation="/skill add ./my-skill"),
                Example(invocation="/skill add --url https://github.com/me/skill.git"),
            ),
        ),
        SubCommand(
            name="rm",
            summary="Delete a non-builtin skill",
            description=(
                "You permanently remove an installed, user, or learned skill. Built-ins "
                "are protected — disable them instead. Append YES to confirm."
            ),
            args=(
                Arg(name="name", summary="skill name"),
                Arg(name="YES", required=False, summary="confirm the removal"),
            ),
            examples=(Example(invocation="/skill rm old-skill YES"),),
        ),
        SubCommand(
            name="edit",
            summary="Print the path to a skill's SKILL.md",
            description=(
                "You get the on-disk path to edit a skill yourself. Built-ins are "
                "read-only; fork with add first. Run reload when you are done."
            ),
            args=(Arg(name="name", summary="skill name"),),
            examples=(Example(invocation="/skill edit research"),),
        ),
        SubCommand(
            name="diff",
            summary="Show recent audit history for a skill",
            description=(
                "You review the recent mutation history — installs, edits, restores — "
                "with before and after content hashes."
            ),
            args=(Arg(name="name", summary="skill name"),),
            examples=(Example(invocation="/skill diff research"),),
        ),
        SubCommand(
            name="enable",
            summary="Turn a skill on without deleting",
            description="You re-activate a previously disabled skill.",
            args=(Arg(name="name", summary="skill name"),),
            examples=(Example(invocation="/skill enable research"),),
        ),
        SubCommand(
            name="disable",
            summary="Turn a skill off without deleting",
            description=(
                "You hide a skill from selection while keeping it on disk so you can "
                "re-enable it later."
            ),
            args=(Arg(name="name", summary="skill name"),),
            examples=(Example(invocation="/skill disable research"),),
        ),
        SubCommand(
            name="reload",
            summary="Rescan disk and refresh the index",
            description=(
                "You re-scan the skills workspace and rebuild the SQLite index after "
                "editing files by hand."
            ),
            examples=(Example(invocation="/skill reload"),),
        ),
        SubCommand(
            name="restore",
            summary="Roll a skill back to an audited version",
            description=(
                "You recover a prior version of a skill from its audit snapshot. Use "
                "diff to find the version hash, then restore to it."
            ),
            args=(
                Arg(name="name", summary="skill name"),
                Arg(name="--version", required=False, summary="audit hash prefix"),
            ),
            examples=(
                Example(invocation="/skill restore research --version a1b2c3d4"),
            ),
        ),
    ),
)


class SkillCommand(SlashCommand):
    """``/skill`` slash command — see module docstring."""

    def __init__(
        self,
        store: SkillIndexStore | None = None,
        loader: SkillLoader | None = None,
        skills_root: Path | None = None,
        *,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> None:
        # 1. ENTRY
        log.skills.debug(
            "[commands] skill.init: entry",
            extra={"_fields": {
                "skills_root": str(skills_root),
                "has_embedding": embedding_registry is not None,
            }},
        )
        self._store: SkillIndexStore = store  # type: ignore[assignment]  # guarded in handle()
        self._loader: SkillLoader = loader  # type: ignore[assignment]  # guarded in handle()
        self._root: Path = skills_root  # type: ignore[assignment]  # guarded in handle()
        self._embedding_registry = embedding_registry
        # 4. EXIT
        log.skills.debug("[commands] skill.init: exit")

    @property
    def command(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return (
            "Manage skills (list, show, add, rm, edit, diff, "
            "enable/disable, reload)."
        )

    @property
    def meta(self) -> CommandMeta:
        return _SKILL_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        # 1. ENTRY
        log.skills.debug(
            "[commands] skill.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        if self._store is None or self._loader is None or self._root is None:
            return "✗ /skill: not configured"
        stripped = args.strip()
        if not stripped:
            return render_usage("skill", _SKILL_META)
        parts = stripped.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        try:
            # 2. DECISION — dispatch
            if sub == "list":
                result = await self._list(rest.strip())
            elif sub == "show":
                result = await self._show(rest.strip())
            elif sub == "add":
                result = await self._add(rest.strip())
            elif sub == "rm":
                result = await self._rm(rest.strip())
            elif sub == "edit":
                result = await self._edit(rest.strip())
            elif sub == "diff":
                result = await self._diff(rest.strip())
            elif sub == "enable":
                result = await self._set_enabled(rest.strip(), enabled=True)
            elif sub == "disable":
                result = await self._set_enabled(rest.strip(), enabled=False)
            elif sub == "reload":
                result = await self._reload()
            elif sub == "restore":
                result = await self._restore(rest.strip())
            elif sub == "menu":
                result = await self._menu(rest.strip())
            else:
                log.skills.debug(
                    "[commands] skill.handle: decision — unknown subcommand",
                    extra={"_fields": {"sub": sub[:40]}},
                )
                return render_usage("skill", _SKILL_META)
        except SkillInstallError as exc:  # user-facing, expected
            log.skills.warning(
                "[commands] skill.handle: install failed",
                extra={"_fields": {"sub": sub, "reason": str(exc)}},
            )
            return f"✗ /skill {sub}: {exc}"
        except Exception as exc:  # B5
            log.skills.error(
                "[commands] skill.handle: subcommand crashed",
                exc_info=exc, extra={"_fields": {"sub": sub}},
            )
            return f"✗ /skill {sub}: {exc}"
        # 4. EXIT
        out_text = result.text if isinstance(result, CommandResponse) else result
        log.skills.debug(
            "[commands] skill.handle: exit",
            extra={"_fields": {"sub": sub, "out_len": len(out_text)}},
        )
        return result

    # ----- subcommands --------------------------------------------------------

    async def _list(self, args: str) -> str | CommandResponse:
        # 1. ENTRY
        log.skills.debug("[commands] skill.list: entry",
                         extra={"_fields": {"args": args[:40]}})
        # 2. DECISION — optional --source filter
        source_filter: SkillSource | None = None
        tokens = args.split()
        if "--source" in tokens:
            i = tokens.index("--source")
            if i + 1 >= len(tokens):
                return "Usage: /skill list [--source builtin|installed|user|learned]"
            cand = tokens[i + 1]
            if cand not in _VALID_SOURCES:
                return (f"✗ /skill list: invalid source {cand!r}, "
                        f"must be one of {', '.join(_VALID_SOURCES)}")
            source_filter = cand
        # 3. STEP — query SQLite index. We want everything (enabled + disabled)
        # so users can see what they've toggled off; pull each source explicitly.
        if source_filter is not None:
            skills = await self._store.list_for_source(source_filter)
        else:
            skills = []
            for src in _VALID_SOURCES:
                skills.extend(await self._store.list_for_source(src))
        if not skills:
            log.skills.debug("[commands] skill.list: exit — empty")
            return CommandResponse(
                text="No skills installed yet.",
                actions=(Action(label="+ Add skill", command="/skill add", destructive=False),),
            )
        # 4. EXIT — format
        lines = ["Skills:"]
        actions = [Action(label="+ Add skill", command="/skill add", destructive=False)]
        for s in skills:
            flag = " " if s.enabled else "✗"
            rate = "" if s.success_rate is None else f"  ({s.success_rate:.2f})"
            lines.append(
                f"  {flag} [{s.source:9}] {s.name}  v{s.version}{rate}  "
                f"— {s.description[:60]}",
            )
            actions.append(Action(label=s.name, command=f"/skill menu {s.name}", destructive=False))
        out = "\n".join(lines)
        log.skills.debug("[commands] skill.list: exit",
                         extra={"_fields": {"n": len(skills)}})
        return CommandResponse(text=out, actions=tuple(actions))

    async def _menu(self, args: str) -> str | CommandResponse:
        log.skills.debug("[commands] skill.menu: entry", extra={"_fields": {"name": args[:60]}})
        if not args:
            return "Usage: /skill menu <name>"
        sk = await self._find_one(args)
        if sk is None:
            return f"✗ Skill '{args}' not found"
        text = (
            f"{sk.name}  [{sk.source}]  v{sk.version}  enabled={sk.enabled}\n"
            f"{sk.description[:120]}"
        )
        toggle_verb = "disable" if sk.enabled else "enable"
        actions = [
            Action(label="Show", command=f"/skill show {sk.name}", destructive=False),
            Action(label="Diff", command=f"/skill diff {sk.name}", destructive=False),
            Action(
                label=toggle_verb.capitalize(),
                command=f"/skill {toggle_verb} {sk.name}",
                destructive=False,
            ),
        ]
        if sk.source != "builtin":
            actions.append(
                Action(label="Edit", command=f"/skill edit {sk.name}", destructive=False)
            )
            actions.append(
                Action(
                    label=f"Remove {sk.name}",
                    command=f"/skill rm {sk.name} {_CONFIRMATION}",
                    destructive=True,
                )
            )
        log.skills.debug(
            "[commands] skill.menu: exit", extra={"_fields": {"name": sk.name}}
        )
        return CommandResponse(text=text, actions=tuple(actions))

    async def _show(self, args: str) -> str:
        log.skills.debug("[commands] skill.show: entry",
                         extra={"_fields": {"name": args[:60]}})
        if not args:
            return "Usage: /skill show <name>"
        sk = await self._find_one(args)
        if sk is None:
            return f"✗ /skill show: no skill matching {args!r}"
        lines = [
            f"Skill: {sk.name}  [{sk.source}]  v{sk.version}",
            f"  Path: {sk.path}",
            f"  Description: {sk.description}",
        ]
        if sk.when_to_use:
            lines.append(f"  When to use: {sk.when_to_use}")
        if sk.success_rate is not None:
            lines.append(
                f"  Stats: {sk.n_executions} runs, success_rate={sk.success_rate:.2f}",
            )
        lines.append(f"  Enabled: {sk.enabled}")
        if sk.body_text:
            lines.append("")
            lines.append("─" * 60)
            lines.append(sk.body_text)
        log.skills.debug("[commands] skill.show: exit",
                         extra={"_fields": {"name": sk.name, "body_len": len(sk.body_text)}})
        return "\n".join(lines)

    async def _add(self, args: str) -> str:
        log.skills.info("[commands] skill.add: entry",
                        extra={"_fields": {"args_len": len(args)}})
        if not args:
            return "Usage: /skill add <local-path>   OR   /skill add --url <url>"
        # 2. DECISION — URL vs local
        if args.startswith("--url"):
            url = args[len("--url"):].strip()
            if not url:
                return "Usage: /skill add --url <url>"
            if url.endswith(".git") or url.startswith("git@"):
                result = await install_from_git_url(url, self._root)
                actor_kind = "git"
            elif url.startswith("http://") or url.startswith("https://"):
                # Try git if URL points to a git host repo path, else archive.
                if _looks_like_git_repo(url):
                    result = await install_from_git_url(url, self._root)
                    actor_kind = "git"
                else:
                    result = await install_from_archive_url(url, self._root)
                    actor_kind = "archive"
            else:
                return f"✗ /skill add: unsupported URL scheme: {url}"
        else:
            src_path = Path(args).expanduser()
            result = await install_from_local_path(src_path, self._root)
            actor_kind = "local"
        # 3. STEP — refresh index + audit through the provenance chokepoint
        # (snapshot included so /skill restore can roll forward to this version).
        async def _reindex() -> None:
            await reindex_after_change(
                self._loader, self._store, self._root,
                embedding_registry=self._embedding_registry,
            )

        await record_skill_mutation(
            self._store,
            skill_name=result.name, source="installed", op="create",
            actor=f"user:{actor_kind}", target_dir=result.path,
            mutate=_reindex, snapshot_when="after",
            details={"path": str(result.path)},
        )
        # 4. EXIT
        log.skills.info(
            "[commands] skill.add: exit",
            extra={"_fields": {"final_name": result.name, "kind": actor_kind}},
        )
        return f"✓ Installed skill '{result.name}' from {actor_kind} → {result.path}"

    async def _rm(self, args: str) -> str:
        log.skills.debug("[commands] skill.rm: entry",
                         extra={"_fields": {"args": args[:60]}})
        if not args:
            return "Usage: /skill rm <name> [YES]"
        parts = args.split(maxsplit=1)
        name = parts[0]
        confirmation = parts[1].strip() if len(parts) > 1 else ""
        sk = await self._find_one(name)
        if sk is None:
            return f"✗ /skill rm: no skill matching {name!r}"
        if sk.source == "builtin":
            return ("✗ /skill rm: cannot remove built-in skills. "
                    "Use `/skill disable` to hide one.")
        if confirmation != _CONFIRMATION:
            return (f"Confirm removal of '{sk.name}' ({sk.source}) at {sk.path}.\n"
                    f"   Type: /skill rm {sk.name} YES to proceed.")
        path_to_delete = Path(sk.path)

        # 3. STEP — delete from disk + index through the provenance chokepoint.
        # snapshot_when="before" so /skill restore can resurrect the dir.
        async def _delete() -> None:
            shutil.rmtree(path_to_delete, ignore_errors=True)
            await self._store.delete(sk.skill_id)

        await record_skill_mutation(
            self._store,
            skill_name=sk.name, source=sk.source, op="delete",
            actor="user:rm", target_dir=path_to_delete,
            mutate=_delete, snapshot_when="before",
            details={"path": str(path_to_delete)},
        )
        log.skills.info(
            "[commands] skill.rm: exit",
            extra={"_fields": {"name": sk.name, "source": sk.source}},
        )
        return f"✓ Removed skill '{sk.name}' ({sk.source})"

    async def _edit(self, args: str) -> str:
        log.skills.debug("[commands] skill.edit: entry",
                         extra={"_fields": {"name": args[:60]}})
        if not args:
            return "Usage: /skill edit <name>"
        sk = await self._find_one(args)
        if sk is None:
            return f"✗ /skill edit: no skill matching {args!r}"
        if sk.source == "builtin":
            return ("✗ /skill edit: built-in skills are read-only. "
                    "Use `/skill add` to fork or copy first.")
        skill_md = Path(sk.path) / "SKILL.md"
        return (
            f"Open this file in your editor:\n  {skill_md}\n\n"
            f"When done, run `/skill reload` to re-scan the workspace."
        )

    async def _diff(self, args: str) -> str:
        log.skills.debug("[commands] skill.diff: entry",
                         extra={"_fields": {"name": args[:60]}})
        if not args:
            return "Usage: /skill diff <name>"
        sk = await self._find_one(args)
        if sk is None:
            return f"✗ /skill diff: no skill matching {args!r}"
        entries = await self._store.recent_audit_for_skill(sk.name, limit=20)
        if not entries:
            return f"No audit history for '{sk.name}'."
        lines = [f"Audit history for '{sk.name}' ({sk.source}):"]
        for e in entries:
            ts = datetime.fromtimestamp(e.ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            line = f"  {ts}  {e.op:8} actor={e.actor}"
            if e.before_hash and e.after_hash:
                line += f"  {e.before_hash[:8]}→{e.after_hash[:8]}"
            elif e.after_hash:
                line += f"  → {e.after_hash[:8]}"
            elif e.before_hash:
                line += f"  {e.before_hash[:8]} →"
            lines.append(line)
        log.skills.debug("[commands] skill.diff: exit",
                         extra={"_fields": {"name": sk.name, "n": len(entries)}})
        return "\n".join(lines)

    async def _set_enabled(self, args: str, *, enabled: bool) -> str:
        verb = "enable" if enabled else "disable"
        log.skills.debug(f"[commands] skill.{verb}: entry",
                         extra={"_fields": {"name": args[:60]}})
        if not args:
            return f"Usage: /skill {verb} <name>"
        sk = await self._find_one(args)
        if sk is None:
            return f"✗ /skill {verb}: no skill matching {args!r}"
        # Enable/disable is a metadata toggle with no content snapshot — it does
        # not route through record_skill_mutation (which is the content-mutation
        # provenance chokepoint). The audit row carries no before/after hash, as
        # before.
        await self._store.set_enabled(sk.skill_id, enabled=enabled)
        await self._store.audit_write(
            skill_name=sk.name, source=sk.source, op=verb,
            actor=f"user:{verb}",
        )
        log.skills.info(f"[commands] skill.{verb}: exit",
                        extra={"_fields": {"name": sk.name}})
        return f"✓ Skill '{sk.name}' {verb}d"

    async def _reload(self) -> str:
        log.skills.info("[commands] skill.reload: entry")
        loaded = await reindex_after_change(
            self._loader, self._store, self._root,
            embedding_registry=self._embedding_registry,
        )
        log.skills.info("[commands] skill.reload: exit",
                        extra={"_fields": {"loaded": len(loaded)}})
        return f"✓ Reloaded — {len(loaded)} skill(s) on disk"

    async def _restore(self, args: str) -> str:
        # 1. ENTRY
        log.skills.info("[commands] skill.restore: entry",
                        extra={"_fields": {"args_len": len(args)}})
        # 2. DECISION — parse args
        parts = args.split()
        if len(parts) < 1 or not parts[0]:
            return ("Usage: /skill restore <name> --version <hash-prefix>\n"
                    "       Use /skill diff <name> to see available hashes.")
        name = parts[0]
        version: str | None = None
        if "--version" in parts:
            i = parts.index("--version")
            if i + 1 < len(parts):
                version = parts[i + 1]
        if not version:
            return await self._restore_list_versions(
                name, reason="missing --version flag",
            )
        # 3. STEP — look up the requested version
        entry = await self._store.find_audit_by_hash(name, version)
        if entry is None:
            return await self._restore_list_versions(
                name, reason=f"no audit entry matches hash {version!r}",
            )
        if not entry.snapshot:
            return (f"✗ /skill restore: audit entry {entry.audit_id} "
                    f"({entry.op} by {entry.actor}) has no snapshot — "
                    f"this op didn't change file content.")
        if entry.source == "builtin":
            return "✗ /skill restore: built-in skills are read-only."
        # Compute current state for the audit trail.
        target_dir = self._root / entry.source / name
        before = hash_dir(target_dir) if target_dir.exists() else None
        # 3. STEP — restore the file tree
        try:
            restore_snapshot(target_dir, entry.snapshot)
        except Exception as exc:  # B5
            log.skills.error(
                "[commands] skill.restore: restore_snapshot failed",
                exc_info=exc, extra={"_fields": {"name": name, "version": version}},
            )
            return f"✗ /skill restore: write failed: {exc}"
        # Re-index + re-embed, then audit through the provenance chokepoint.
        # before-hash was captured above (the live tree pre-overwrite); the
        # snapshot is the restored audit entry's own snapshot, reused verbatim.
        async def _reindex() -> None:
            await reindex_after_change(
                self._loader, self._store, self._root,
                embedding_registry=self._embedding_registry,
            )

        await record_skill_mutation(
            self._store,
            skill_name=name, source=entry.source, op="restore",
            actor="user:restore", target_dir=target_dir,
            mutate=_reindex, snapshot_when="after",
            snapshot=entry.snapshot, before_hash=before,
            details={
                "restored_from_audit_id": entry.audit_id,
                "restored_from_op": entry.op,
                "restored_from_actor": entry.actor,
                "restored_hash": version,
            },
        )
        # 4. EXIT
        log.skills.info(
            "[commands] skill.restore: exit",
            extra={"_fields": {
                "name": name, "restored_from_audit_id": entry.audit_id,
                "files": len(entry.snapshot),
            }},
        )
        return (f"✓ Restored '{name}' to audit entry {entry.audit_id} "
                f"({entry.op} by {entry.actor}, {len(entry.snapshot)} file(s)).")

    async def _restore_list_versions(self, name: str, *, reason: str) -> str:
        """Pretty-print available restore versions when the user's --version
        misses (or wasn't provided). Per operator vote in 3e."""
        entries = await self._store.recent_audit_for_skill(name, limit=20)
        if not entries:
            return f"✗ /skill restore: {reason}; no audit history for '{name}'."
        lines = [
            f"✗ /skill restore: {reason}",
            "",
            f"Recent versions of '{name}' you can restore (newest first):",
        ]
        for e in entries:
            if not e.snapshot:
                continue
            ts = datetime.fromtimestamp(e.ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            hash_shown = (e.after_hash or e.before_hash or "")[:12]
            lines.append(
                f"  {ts}  {e.op:9} by {e.actor:24}  --version {hash_shown}",
            )
        lines.append("")
        lines.append(f"Try: /skill restore {name} --version <hash-prefix>")
        return "\n".join(lines)

    # ----- internals ----------------------------------------------------------

    async def _find_one(self, name: str) -> Skill | None:
        """Locate a skill by name across all sources (first hit wins).

        ``learned`` is searched LAST so a builtin/installed/user skill with the
        same name always shadows a learned one (the human-authored intent
        wins). Returns ``None`` if no source has it.
        """
        for src in _VALID_SOURCES:
            sk = await self._store.get(src, name)
            if sk is not None:
                return sk
        return None

    @classmethod
    def create_and_register(
        cls,
        store: SkillIndexStore,
        loader: SkillLoader,
        skills_root: Path,
        *,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> SkillCommand:
        """Construct a :class:`SkillCommand` and register it on the singleton."""
        cmd = cls(
            store=store, loader=loader, skills_root=skills_root,
            embedding_registry=embedding_registry,
        )
        CommandRegistry.instance().register(cmd)
        return cmd


def _looks_like_git_repo(url: str) -> bool:
    """Heuristic: treat a URL as a git repo if it ends in ``.git``, starts with
    ``git@``, or its host is a known git forge with at least two non-empty path
    segments (owner/repo).  Trailing slashes and extra path segments (e.g.
    ``.../owner/repo/tree/main``) are tolerated.
    """
    # Explicit git markers take priority over archive extensions.
    if url.endswith(".git") or url.startswith("git@"):
        return True
    git_hosts = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
    for host in git_hosts:
        if f"://{host}/" in url:
            tail = url.split(f"://{host}/", 1)[1].rstrip("/")
            # owner/repo or deeper (e.g. owner/repo/tree/main) → repo URL
            segs = [s for s in tail.split("/") if s]
            if len(segs) >= 2:
                return True
    return False
