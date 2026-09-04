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
from typing import TYPE_CHECKING, cast, get_args

from stackowl.commands.base import SlashCommand
from stackowl.commands.dry_run import strip_sigil
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
from stackowl.skills import standard as std
from stackowl.skills import standard_migration as migration
from stackowl.skills.loader import SkillLoader
from stackowl.skills.manifest import SkillSource
from stackowl.skills.store import Skill, SkillIndexStore
from stackowl.skills.use_prompt import build_use_prompt

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.pipeline.state import PipelineState
    from stackowl.providers.registry import ProviderRegistry


_CONFIRMATION = "YES"
#: Derived, not restated. D10.1 collapsed the loader's copy onto the manifest's
#: Literal and this file kept a THIRD — plus the `choices=` tuple below, a
#: fourth. Four copies of one vocabulary, agreeing by luck: a source added to the
#: manifest alone is unreachable from `/skill`, and one added here alone offers
#: the operator a filter that can match nothing.
_VALID_SOURCES: tuple[SkillSource, ...] = get_args(SkillSource)

_SKILL_META = CommandMeta(
    grammar="verb",
    group="Memory & Knowledge",
    subcommands=(
        SubCommand(
            name="use",
            summary="Apply a skill to what you are doing",
            description=(
                "The agent loads the named skill and follows it for this turn. This is "
                "the one verb that USES a skill; every other verb here manages them. "
                "`show` prints a skill to YOUR screen — `use` puts it in front of the "
                "agent. Names may be bare or qualified as source:name, and hyphens are "
                "fine."
            ),
            args=(
                Arg(name="name", summary="skill name, bare or source:name"),
                Arg(
                    name="instruction",
                    required=False,
                    summary="what to apply it to",
                ),
            ),
            examples=(
                Example(invocation="/skill use deep-research"),
                Example(invocation="/skill use deep-research compare the two vendors"),
                Example(invocation="/skill use builtin:verify-before-claim"),
            ),
        ),
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
                    choices=_VALID_SOURCES,
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
            name="dedupe",
            summary="Collapse -N duplicate families into one skill",
            description=(
                "You preview, then optionally apply, the merge of numbered duplicate "
                "families (foo, foo-1, foo-2 ...) into a single skill. The most-used "
                "member survives under the base name and inherits the family's total "
                "executions; a pinned member wins outright. Everything removed is "
                "copied to a timestamped archive outside the catalog first.\n\n"
                "PREVIEWS BY DEFAULT. This is the only irreversible operation in the "
                "skill lifecycle, so it does nothing until you pass --apply."
            ),
            args=(
                Arg(name="--apply", required=False,
                    summary="carry the plan out (default: preview only)"),
            ),
            examples=(
                Example(invocation="/skill dedupe"),
                Example(invocation="/skill dedupe --apply"),
            ),
        ),
        SubCommand(
            name="migrate",
            summary="Rewrite pre-standard skills to the authoring standard",
            description=(
                "You bring existing skills up to the current authoring standard: a "
                "<=60-character description, a rich when_to_use, and the seven required "
                "body sections. Each rewrite is one LLM call, the original is archived "
                "first, and a rewrite that fails the standard is REFUSED — the original "
                "file is left exactly as it was.\n\n"
                "PREVIEWS BY DEFAULT and works in bounded batches, because this is the "
                "only pass that rewrites what a skill says."
            ),
            args=(
                Arg(name="--apply", required=False,
                    summary="carry the rewrites out (default: preview only)"),
                Arg(name="--limit", required=False,
                    summary="how many skills this run may rewrite (default 10)"),
            ),
            examples=(
                Example(invocation="/skill migrate"),
                Example(invocation="/skill migrate --apply --limit 20"),
            ),
        ),
        SubCommand(
            name="menu",
            summary="Show a skill with one-tap action buttons",
            description=(
                "You get a compact card for one skill — source, version, enable state "
                "and description — with buttons for show, diff, enable/disable and "
                "delete. Undeclared until 2026-08-29, so it worked but was invisible "
                "to /help and /find."
            ),
            args=(Arg(name="name", summary="skill name"),),
            examples=(Example(invocation="/skill menu research"),),
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

    def build_turn_prompt(self, args: str) -> str | None:
        """D10.5 — ``use`` contributes a PROMPT; the other twelve verbs reply.

        THE FIRST CONDITIONAL ``build_turn_prompt`` in the tree, and deliberate
        rather than incidental. The seam's contract is "this text is the turn's
        input", and that is true of exactly one verb here: ``use`` steers the agent,
        while ``list``/``show``/``rm``/... answer the operator. Returning ``None``
        for the rest is what keeps them byte-for-byte unchanged.

        Never raises: the gateway already treats a raising builder as "stays an
        ordinary command", and this must not be the thing that costs someone their
        ``/skill list``.
        """
        try:
            # THE DRY-RUN HOLE, closed here because it cannot be closed downstream.
            # `??` is intercepted in CommandRegistry.dispatch, and this seam runs
            # BEFORE dispatch — so without this check `/skill use X ??` would STEER
            # the model instead of previewing, which is precisely the opposite of
            # what the operator asked for. Returning None hands the turn back to
            # ordinary dispatch, which then renders the preview. `strip_sigil` is
            # asked rather than re-implemented: one source for what `??` means.
            is_dry_run, cleaned = strip_sigil(args)
            if is_dry_run:
                log.skills.debug(
                    "[commands] skill.build_turn_prompt: dry-run — deferring to dispatch",
                    extra={"_fields": {"args": cleaned[:60]}},
                )
                return None
            head, _, rest = cleaned.strip().partition(" ")
            if head.lower() != "use":
                return None
            return build_use_prompt(rest)
        except Exception as exc:  # never let the seam cost a command
            log.skills.warning(
                "[commands] skill.build_turn_prompt: failed — staying an ordinary command",
                exc_info=exc,
                extra={"_fields": {"args": args[:60]}},
            )
            return None

    def __init__(
        self,
        store: SkillIndexStore | None = None,
        loader: SkillLoader | None = None,
        skills_root: Path | None = None,
        *,
        embedding_registry: EmbeddingRegistry | None = None,
        provider_registry: object | None = None,
        consent_gate: object | None = None,
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
        # Both optional and both only used by `migrate`, which fails closed and
        # says so when they are missing — an unwired dependency must not turn
        # "apply" into a silent preview.
        self._provider_registry = provider_registry
        self._consent_gate = consent_gate
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

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:  # noqa: C901
        # 1. ENTRY
        log.skills.debug(
            "[commands] skill.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_key}},
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
            elif sub == "migrate":
                result = await self._migrate(rest.strip())
            elif sub == "dedupe":
                result = await self._dedupe(rest.strip())
            elif sub == "restore":
                result = await self._restore(rest.strip())
            elif sub == "menu":
                result = await self._menu(rest.strip())
            elif sub == "use":
                # Reached only where the turn-prompt seam is NOT wired (the gateway
                # intercepts `use` before dispatch). Mirrors /learn: show what WOULD
                # have run rather than silently doing nothing.
                built = build_use_prompt(rest.strip())
                result = built or render_usage("skill", _SKILL_META)
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

    async def _migrate(self, args: str) -> str:
        """Preview or apply authoring-standard migration."""
        # 1. ENTRY
        log.skills.debug("[commands] skill.migrate: entry",
                         extra={"_fields": {"args": args[:60]}})
        apply = False
        limit = migration.DEFAULT_LIMIT
        tokens = args.split()
        i = 0
        while i < len(tokens):
            if tokens[i] == "--apply":
                apply = True
            elif tokens[i] == "--limit" and i + 1 < len(tokens):
                i += 1
                if not tokens[i].isdigit():
                    return f"✗ /skill migrate: --limit needs a number, got {tokens[i]!r}"
                limit = int(tokens[i])
            else:
                return (f"✗ /skill migrate: unknown option {tokens[i]!r}. "
                        f"Use --apply and/or --limit N.")
            i += 1

        if self._provider_registry is None:
            # Fails CLOSED and says why. Silently previewing when the caller
            # asked to apply would read as "there was nothing to migrate".
            return ("✗ /skill migrate: no provider registry wired — migration "
                    "rewrites content and needs a model.")
        registry = cast("ProviderRegistry", self._provider_registry)
        provider, model = registry.get_with_cascade("fast")

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        report = await migration.SkillStandardMigrator(
            self._store, provider,
            archive_root=self._root.parent / "pre-migration",
            model=model, consent_gate=self._consent_gate,
        ).run(apply=apply, limit=limit, stamp=stamp)

        if not report.outcomes:
            return (f"✓ /skill migrate: every skill already meets standard "
                    f"v{std.STANDARD_VERSION}.")

        lines = [f"{'Applied' if report.applied else 'PREVIEW'} — {report.summary()}", ""]
        lines += [o.describe() for o in report.outcomes]
        if report.applied and report.archive_path is not None:
            lines += ["", f"Originals archived: {report.archive_path}"]
        elif not report.applied:
            lines += ["", "Nothing changed. Re-run with --apply to carry this out."]
        if report.remaining:
            lines.append(f"{report.remaining} skill(s) still to migrate — re-run to continue.")

        # 4. EXIT
        log.skills.info("[commands] skill.migrate: exit",
                        extra={"_fields": {"apply": apply, "migrated": report.migrated,
                                           "failed": report.failed,
                                           "remaining": report.remaining}})
        return "\n".join(lines)

    async def _dedupe(self, args: str) -> str:
        """Preview or apply ``-N`` family consolidation.

        The ``--apply`` flag is required to act. That asymmetry is the point:
        every other retirement path here is reversible, this one deletes.
        """
        # 1. ENTRY
        log.skills.debug("[commands] skill.dedupe: entry",
                         extra={"_fields": {"args": args[:40]}})
        apply = args.strip() == "--apply"
        if args.strip() and not apply:
            return (f"✗ /skill dedupe: unknown option {args.strip()!r}. "
                    f"Use /skill dedupe or /skill dedupe --apply.")

        from stackowl.skills.consolidation import SkillConsolidator

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        plan = await SkillConsolidator(self._store, self._root).run(
            apply=apply, stamp=stamp,
        )

        if not plan.families and not plan.skipped:
            return "✓ /skill dedupe: no numbered duplicate families found."

        lines = [f"{'Applied' if plan.applied else 'PREVIEW'} — {plan.summary()}", ""]
        for family in plan.families[:40]:
            lines.append(f"  {family.describe()}")
            # Name what goes, capped. "drop 20" without the names is not a
            # preview anyone can approve.
            lines.append(f"      dropping: {', '.join(family.removed[:6])}"
                         + (f" (+{len(family.removed) - 6} more)"
                            if len(family.removed) > 6 else ""))
        if len(plan.families) > 40:
            lines.append(f"  ... and {len(plan.families) - 40} more families")
        for skip in plan.skipped:
            lines.append(f"  skipped: {skip}")
        if plan.applied and plan.archive_path is not None:
            lines += ["", f"Archive: {plan.archive_path}",
                      "Run /skill reload to refresh the index from disk."]
        elif not plan.applied:
            lines += ["", "Nothing changed. Re-run with --apply to carry this out."]

        # 4. EXIT
        log.skills.info("[commands] skill.dedupe: exit",
                        extra={"_fields": {"apply": apply,
                                           "families": len(plan.families),
                                           "rows_removed": plan.rows_removed}})
        return "\n".join(lines)

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
