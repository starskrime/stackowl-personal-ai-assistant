"""OwlsCommand — base class for owl persona management, inherited by the
live ``/owl`` command (:class:`OwlCommand`, below).

Subcommands: ``list``, ``create``, ``edit``, ``remove``, ``health``, ``dna``.
(``add`` was retired in Task 7 — folded into ``create``/``owl_build``.)

The command takes its dependencies via constructor injection so the wiring
layer can decide whether to give it a real :class:`OwlRegistry`, a real
:class:`DbPool` and a real :class:`EventBus`, or ``None`` in test mode.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import config_path, load_yaml, save_yaml
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.commands.owls_helpers import (
    format_dna_display,
    format_owl_roster,
    manifest_to_yaml_entry,
    parse_edit_args,
    parse_owl_build_flags,
)
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import Action, CommandResponse
from stackowl.exceptions import (
    CommandParseError,
    ManifestValidationError,
    OwlNotFoundError,
)
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.objectives.store import ObjectiveNotFoundError, ObjectiveStore
from stackowl.owls.registry import _SECRETARY_NAME
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.state import PipelineState
    from stackowl.tools.registry import ToolRegistry

_OWLS_META = CommandMeta(
    grammar="verb",
    group="Owls",
    subcommands=(
        SubCommand(
            name="list",
            summary="Show registered owls",
        ),
        SubCommand(
            name="create",
            summary="Create an owl from a free-text description",
            description=(
                "You describe the owl in plain language; any missing details "
                "(name, capability, specialty, schedule) are elicited "
                "interactively, the same way owl creation works in chat."
            ),
            args=(Arg(name="text", summary="free-text description of the owl"),),
            examples=(
                Example(
                    invocation="/owls create a research assistant that reads arxiv daily",
                    note="Free-text owl creation, elicits any missing fields",
                ),
            ),
        ),
        SubCommand(
            name="edit",
            summary="Update fields on an existing owl",
            description="You change one or more fields on an owl and re-validate the manifest.",
            args=(
                Arg(name="name", summary="owl name"),
                Arg(name="--tier", required=False, summary="model tier"),
            ),
            examples=(
                Example(invocation="/owls edit Sage --tier powerful"),
            ),
        ),
        SubCommand(
            name="remove",
            summary="Permanently remove an owl",
            description="You deregister an owl and drop its config and DNA rows. Confirmed with YES.",
            args=(Arg(name="name", summary="owl name"),),
            examples=(
                Example(invocation="/owls remove Sage YES", note="Confirm removal"),
            ),
        ),
        SubCommand(
            name="health",
            summary="Report owl registry health",
        ),
        SubCommand(
            name="dna",
            summary="Show DNA traits, current versus authored",
            args=(Arg(name="name", summary="owl name"),),
        ),
        SubCommand(
            name="reset-dna",
            summary="Revert evolved DNA to the authored baseline",
            description="You discard accumulated evolution and restore the owl's authored DNA. Confirmed with YES.",
            args=(Arg(name="name", summary="owl name"),),
            examples=(
                Example(invocation="/owls reset-dna Sage YES", note="Confirm reset"),
            ),
        ),
        SubCommand(
            name="objectives",
            summary="List standing objectives and their progress",
        ),
        SubCommand(
            name="objective",
            summary="Show one objective's steps and activity log",
            args=(Arg(name="objective_id", summary="objective id"),),
            examples=(
                Example(invocation="/owls objective obj-1a2b3c4d"),
            ),
        ),
        SubCommand(
            name="objective-cancel",
            summary="Abandon a standing objective",
            description="You stop the assistant from pursuing an objective. Confirmed with YES.",
            args=(Arg(name="objective_id", summary="objective id"),),
            examples=(
                Example(invocation="/owls objective-cancel obj-1a2b3c4d YES", note="Confirm"),
            ),
        ),
    ),
)

_NO_REGISTRY = "(no owl registry wired — start StackOwl normally to manage owls)"
_NO_OBJECTIVE_DB = "(no database wired — start StackOwl normally to manage objectives)"
#: Status → glyph for the step list (symbols, not language — i18n-safe).
_STEP_GLYPH = {"done": "✓", "running": "…", "failed": "✗", "blocked": "⏸", "pending": "·"}

_SELECT_DNA_SQL = (
    "SELECT challenge_level, verbosity, curiosity, formality, creativity, "
    "precision, updated_at FROM owl_dna WHERE owl_name = ?"
)
_DELETE_DNA_SQL = "DELETE FROM owl_dna WHERE owl_name = ?"
_DELETE_CHECKPOINTS_SQL = "DELETE FROM dna_checkpoints WHERE owl_name = ?"


class OwlsCommand(SlashCommand):
    """Implements ``/owls [list|add|remove|health|dna]``."""

    def __init__(
        self,
        owl_registry: OwlRegistry | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._registry = owl_registry
        self._db = db
        self._bus = event_bus
        self._tool_registry = tool_registry

    @property
    def command(self) -> str:
        return "owls"

    @property
    def description(self) -> str:
        return "Manage owl personas: list, create, remove, health, dna."

    @property
    def meta(self) -> CommandMeta:
        return _OWLS_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        log.gateway.debug(
            "[commands] owls.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "list":
                result = self._list()
            elif sub == "create":
                result = await self._create_freetext(rest, state)
            elif sub == "edit":
                result = await self._edit(rest)
            elif sub == "remove":
                result = await self._remove(rest)
            elif sub == "health":
                result = await self._health()
            elif sub == "dna":
                result = await self._dna(rest)
            elif sub == "reset-dna":
                result = await self._reset_dna(rest)
            elif sub == "objectives":
                result = await self._objectives()
            elif sub == "objective":
                result = await self._objective(rest)
            elif sub == "objective-cancel":
                result = await self._objective_cancel(rest)
            else:
                log.gateway.debug(
                    "[commands] owls.handle: unknown subcommand",
                    extra={"_fields": {"sub": sub}},
                )
                return render_usage("owls", _OWLS_META)
        except CommandParseError as exc:
            log.gateway.warning(
                "[commands] owls.handle: parse error",
                extra={"_fields": {"sub": sub, "error": str(exc)}},
            )
            return f"✗ {exc}\n\n{render_usage('owls', _OWLS_META)}"
        except (ManifestValidationError, OwlNotFoundError) as exc:
            log.gateway.warning(
                "[commands] owls.handle: domain error",
                extra={"_fields": {"sub": sub, "error": str(exc)}},
            )
            return f"✗ /owls {sub}: {exc}"
        except Exception as exc:
            log.gateway.error(
                "[commands] owls.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /owls {sub}: {exc}"
        log.gateway.debug("[commands] owls.handle: exit", extra={"_fields": {"sub": sub}})
        return result

    # ------------------------------------------------------------------ list
    def _list(self) -> str | CommandResponse:
        log.gateway.debug("[commands] owls.list: entry")
        if self._registry is None:
            return _NO_REGISTRY
        manifests = self._registry.list()
        text = format_owl_roster(manifests)
        if not manifests:
            log.gateway.debug("[commands] owls.list: exit — empty")
            return CommandResponse(
                text=text,
                actions=(Action(label="+ Add owl", command=f"/{self.command} create", destructive=False),),
            )
        actions = (Action(label="+ Add owl", command=f"/{self.command} create", destructive=False),) + tuple(
            Action(label=m.display, command=f"/{self.command} menu {m.name}", destructive=False)
            for m in sorted(manifests, key=lambda x: x.display.casefold())
        )
        log.gateway.debug("[commands] owls.list: exit", extra={"_fields": {"count": len(manifests)}})
        return CommandResponse(text=text, actions=actions)

    # ------------------------------------------------------------ create (free text)
    async def _create_freetext(self, rest: str, state: PipelineState) -> str:
        """Mint an owl from a free-text description via the real OwlBuildTool.

        A natural-language slash path onto the SAME create code chat already
        reaches (S6/UniOwl) — no owl-creation logic lives here. Reuses
        owl_build's elicitation, consent gating, DNA baseline capture, YAML
        persistence, and scheduler reconciliation completely unchanged. Sits
        alongside ``add``'s structured ``--role/--tier`` grammar; neither
        subcommand touches the other.
        """
        log.gateway.debug(
            "[commands] owls.create_freetext: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        text = rest.strip()
        if not text:
            raise CommandParseError("owls create", "missing free-text description")

        # Lazy import — owl_build.py imports OwlsCommand at module top level, so a
        # top-level import here would be circular. Also keeps this call easily
        # monkeypatchable at its origin (stackowl.tools.meta.owl_build.OwlBuildTool).
        from stackowl.tools.meta.owl_build import OwlBuildTool

        token = TraceContext.start(
            session_id=state.session_id,
            trace_id=state.trace_id,
            interactive=True,
            channel=state.channel,
            reply_target=state.reply_target,
        )
        try:
            result = await OwlBuildTool().execute(action="create", specialty=text)
        finally:
            TraceContext.reset(token)
        log.gateway.info(
            "[commands] owls.create_freetext: exit",
            extra={"_fields": {"success": result.success}},
        )
        return result.output if result.success else f"✗ /owls create: {result.error}"

    # ------------------------------------------------------------------ edit
    async def _edit(self, rest: str) -> str:
        log.gateway.debug(
            "[commands] owls.edit: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._registry is None:
            return _NO_REGISTRY
        changes = parse_edit_args(rest)
        name = changes.pop("name")
        if name == _SECRETARY_NAME:
            log.gateway.warning(
                "[commands] owls.edit: secretary edit refused",
                extra={"_fields": {"name": name}},
            )
            return f"✗ /owls edit: {name} is mandatory and cannot be edited"
        if not changes:
            return f"✗ /owls edit: no fields given for '{name}' (try --tier/--role/...)"
        current = self._registry.get(name)  # raises OwlNotFoundError → handled in handle()
        log.gateway.debug(
            "[commands] owls.edit: applying changes",
            extra={"_fields": {"name": name, "fields": list(changes.keys())}},
        )
        # Re-validate the WHOLE manifest: model_copy(update=...) skips validation on a
        # frozen model, so an invalid edit could land silently. Merging the current
        # dump with changes and re-validating carries bounds/skills/capability_profile
        # over unchanged while enforcing every field constraint (e.g. the tier Literal).
        updated = type(current).model_validate({**current.model_dump(), **changes})
        self._registry.replace(updated)
        self._upsert_to_yaml(manifest_to_yaml_entry(updated))
        if self._bus is not None:
            self._bus.emit("owl_edited", {"name": updated.name})
        log.gateway.info(
            "[commands] owls.edit: exit",
            extra={"_fields": {"name": updated.name, "tier": updated.model_tier}},
        )
        suffix = "" if self._db is not None else " (DNA not persisted — no DB)"
        return f"✓ owl '{updated.name}' updated (tier={updated.model_tier}){suffix}"

    # ---------------------------------------------------------------- remove
    async def _remove(self, rest: str) -> str:
        log.gateway.debug(
            "[commands] owls.remove: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._registry is None:
            return _NO_REGISTRY
        tokens = rest.split()
        if not tokens:
            return "Usage: /owls remove <name>"
        name = tokens[0]
        confirmed = len(tokens) > 1 and tokens[1] == "YES"
        if not confirmed:
            log.gateway.debug(
                "[commands] owls.remove: awaiting confirmation",
                extra={"_fields": {"name": name}},
            )
            return (
                f"⚠ This will permanently remove owl '{name}'.\n"
                f"   Type: /owls remove {name} YES to confirm."
            )
        # Confirmed — proceed with registry deregister (which guards secretary).
        self._registry.deregister(name)
        self._remove_from_yaml(name)
        await self._delete_dna_rows(name)
        # S10 — TRANSACTIONAL teardown: reconcile so the removed owl's owned
        # scheduler row (if it was a scheduled owl) is deleted in this SAME op.
        # A retired owl with a live job is the exact failure this prevents.
        await self._reconcile_schedules()
        if self._bus is not None:
            self._bus.emit("owl_removed", {"name": name})
        log.gateway.info(
            "[commands] owls.remove: exit",
            extra={"_fields": {"name": name}},
        )
        return f"✓ owl '{name}' removed"

    async def _reconcile_schedules(self) -> None:
        """Re-project owl schedules after a removal (ADR-B / S10). Fail-safe."""
        if self._db is None or self._registry is None:
            return
        try:
            from stackowl.scheduler.owl_lifecycle import reconcile_owl_schedules

            await reconcile_owl_schedules(self._registry, self._db)
        except Exception as exc:  # B5 — never fail the removal on a reconcile hiccup
            log.gateway.error(
                "[commands] owls.remove: schedule reconcile failed — owl already removed",
                exc_info=exc,
                extra={"_fields": {}},
            )

    # ---------------------------------------------------------------- health
    async def _health(self) -> str:
        log.gateway.debug("[commands] owls.health: entry")
        if self._registry is None:
            return _NO_REGISTRY
        status = await self._registry.health_check()
        message = status.message or "all systems healthy"
        log.gateway.debug(
            "[commands] owls.health: exit",
            extra={"_fields": {"status": status.status}},
        )
        return f"Status: {status.status} — {message}"

    # ------------------------------------------------------------------- dna
    async def _dna(self, rest: str) -> str:
        log.gateway.debug(
            "[commands] owls.dna: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._registry is None:
            return _NO_REGISTRY
        name = rest.strip()
        if not name:
            return "Usage: /owls dna <name>"
        manifest = self._registry.get(name)  # raises OwlNotFoundError on miss
        db_row: dict[str, Any] | None = None
        if self._db is not None:
            rows = await self._db.fetch_all(_SELECT_DNA_SQL, (name,))
            db_row = rows[0] if rows else None
        authored = None
        if self._db is not None:
            from stackowl.owls.dna_authored import read_authored_dna
            authored = await read_authored_dna(self._db, name)
        result = format_dna_display(name, manifest.dna, db_row, authored=authored)
        log.gateway.debug(
            "[commands] owls.dna: exit",
            extra={"_fields": {"name": name, "has_db_row": db_row is not None}},
        )
        return result

    # -------------------------------------------------------------- reset-dna
    async def _reset_dna(self, rest: str) -> str:
        log.gateway.debug(
            "[commands] owls.reset_dna: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._registry is None:
            return _NO_REGISTRY
        tokens = rest.split()
        if not tokens:
            return "Usage: /owls reset-dna <name> YES"
        name = tokens[0]
        _ = self._registry.get(name)  # raises OwlNotFoundError on miss → handled by handle()
        confirmed = len(tokens) > 1 and tokens[1] == "YES"
        if not confirmed:
            log.gateway.debug(
                "[commands] owls.reset_dna: awaiting confirmation",
                extra={"_fields": {"name": name}},
            )
            return (
                f"⚠ This reverts owl '{name}' DNA to its authored baseline (evolution discarded).\n"
                f"   Type: /owls reset-dna {name} YES to confirm."
            )
        if self._db is None:
            return "DNA store unavailable."
        from stackowl.owls.directive_latch import DIRECTIVE_LATCH
        from stackowl.owls.dna_authored import read_authored_dna
        from stackowl.owls.dna_hydrator import apply_dna_overlay
        from stackowl.owls.dna_storage import upsert_owl_dna
        authored = await read_authored_dna(self._db, name)
        if authored is None:
            log.gateway.debug(
                "[commands] owls.reset_dna: no authored baseline",
                extra={"_fields": {"name": name}},
            )
            return f"No authored baseline recorded for '{name}' — nothing to reset to."
        await upsert_owl_dna(self._db, name, authored, table="owl_dna")
        apply_dna_overlay(self._registry, name, authored)
        DIRECTIVE_LATCH.reset_owl(name)
        log.gateway.info(
            "[commands] owls.reset_dna: exit — DNA reset to authored baseline",
            extra={"_fields": {"name": name}},
        )
        return f"✓ Owl '{name}' DNA reset to authored baseline."

    # ------------------------------------------------------------ objectives
    async def _objectives(self) -> str:
        """List standing objectives with a done/total progress count."""
        if self._db is None:
            return _NO_OBJECTIVE_DB
        store = ObjectiveStore(self._db, DEFAULT_PRINCIPAL_ID)
        objectives = await store.list_objectives()
        if not objectives:
            return "No objectives yet. Ask me to 'keep an eye on X and handle it'."
        lines = ["Objectives:"]
        for o in objectives:
            subs = await store.list_subgoals(o.objective_id)
            done = sum(1 for s in subs if s.status == "done")
            lines.append(
                f"  {o.objective_id} [{o.status}] {o.intent} ({done}/{len(subs)} done)"
            )
        return "\n".join(lines)

    async def _objective(self, rest: str) -> str:
        """Show one objective's steps and recent activity log."""
        if self._db is None:
            return _NO_OBJECTIVE_DB
        objective_id = rest.strip()
        if not objective_id:
            return "Usage: /owls objective <objective_id>"
        store = ObjectiveStore(self._db, DEFAULT_PRINCIPAL_ID)
        try:
            objective = await store.get(objective_id)
        except ObjectiveNotFoundError:
            return f"✗ no such objective: {objective_id!r}"
        subs = await store.list_subgoals(objective_id)
        events = await store.list_events(objective_id)
        lines = [f"{objective.objective_id} [{objective.status}] {objective.intent}"]
        if objective.blocker:
            lines.append(f"  blocked: {objective.blocker}")
        lines.append("Steps:")
        for s in subs:
            glyph = _STEP_GLYPH.get(s.status, "·")
            lines.append(f"  {glyph} {s.description} [{s.status}]")
        if events:
            lines.append("Recent activity:")
            for e in events[-5:]:
                lines.append(f"  • {e.kind}: {e.detail or ''}".rstrip())
        return "\n".join(lines)

    async def _objective_cancel(self, rest: str) -> str:
        """Abandon a standing objective (confirmed with YES)."""
        if self._db is None:
            return _NO_OBJECTIVE_DB
        tokens = rest.split()
        if not tokens:
            return "Usage: /owls objective-cancel <objective_id> YES"
        objective_id = tokens[0]
        store = ObjectiveStore(self._db, DEFAULT_PRINCIPAL_ID)
        try:
            await store.get(objective_id)
        except ObjectiveNotFoundError:
            return f"✗ no such objective: {objective_id!r}"
        confirmed = len(tokens) > 1 and tokens[1] == "YES"
        if not confirmed:
            return (
                f"⚠ This will abandon objective '{objective_id}'.\n"
                f"   Type: /owls objective-cancel {objective_id} YES to confirm."
            )
        await store.update_status(objective_id, "abandoned")
        await store.append_event(objective_id, "abandoned", "cancelled by owner")
        log.gateway.info(
            "[commands] owls.objective_cancel: abandoned",
            extra={"_fields": {"objective_id": objective_id}},
        )
        return f"✓ objective '{objective_id}' abandoned."

    # ----------------------------------------------------------- yaml helpers
    def _upsert_to_yaml(self, entry: dict[str, Any]) -> None:
        """Insert or replace an owl entry in ``stackowl.yaml``'s ``owls:`` list (by name)."""
        path = config_path()
        data = load_yaml(path)
        owls_list = data.get("owls")
        if not isinstance(owls_list, list):
            owls_list = []
        name = entry.get("name")
        replaced = False
        for i, e in enumerate(owls_list):
            if isinstance(e, dict) and e.get("name") == name:
                owls_list[i] = entry
                replaced = True
                break
        if not replaced:
            owls_list.append(entry)
        data["owls"] = owls_list
        save_yaml(path, data)
        log.gateway.debug(
            "[commands] owls._upsert_to_yaml: written",
            extra={"_fields": {"path": str(path), "name": name, "replaced": replaced}},
        )

    def _remove_from_yaml(self, name: str) -> None:
        """Drop the entry with matching ``name`` from ``stackowl.yaml``'s ``owls:`` list."""
        path = config_path()
        if not path.exists():
            log.gateway.debug(
                "[commands] owls._remove_from_yaml: no config file",
                extra={"_fields": {"path": str(path)}},
            )
            return
        data = load_yaml(path)
        owls_list = data.get("owls")
        if not isinstance(owls_list, list):
            return
        data["owls"] = [entry for entry in owls_list if not (isinstance(entry, dict) and entry.get("name") == name)]
        save_yaml(path, data)
        log.gateway.debug(
            "[commands] owls._remove_from_yaml: written",
            extra={"_fields": {"path": str(path), "name": name}},
        )

    def _add_retired_builtin(self, name: str) -> None:
        """Tombstone a retired builtin persona's name in ``retired_builtin_owls:`` so
        ``OwlRegistry.register_builtin_personas`` does not re-add it next boot."""
        path = config_path()
        data = load_yaml(path)
        retired = data.get("retired_builtin_owls")
        if not isinstance(retired, list):
            retired = []
        if name not in retired:
            retired.append(name)
        data["retired_builtin_owls"] = retired
        save_yaml(path, data)
        log.gateway.debug(
            "[commands] owls._add_retired_builtin: written",
            extra={"_fields": {"path": str(path), "name": name}},
        )

    async def _delete_dna_rows(self, name: str) -> None:
        """Best-effort cleanup of ``owl_dna`` and ``dna_checkpoints`` for the owl."""
        if self._db is None:
            log.gateway.debug(
                "[commands] owls._delete_dna_rows: no db wired",
                extra={"_fields": {"name": name}},
            )
            return
        try:
            await self._db.execute(_DELETE_DNA_SQL, (name,))
            await self._db.execute(_DELETE_CHECKPOINTS_SQL, (name,))
        except Exception as exc:
            log.gateway.warning(
                "[commands] owls._delete_dna_rows: cleanup failed",
                exc_info=exc,
                extra={"_fields": {"name": name}},
            )
            return
        log.gateway.debug(
            "[commands] owls._delete_dna_rows: exit",
            extra={"_fields": {"name": name}},
        )

    # ---------------------------------------------------------------- factory
    @classmethod
    def create_and_register(
        cls,
        owl_registry: OwlRegistry | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> OwlsCommand:
        """Construct an :class:`OwlsCommand` and register it on the singleton."""
        cmd = cls(owl_registry=owl_registry, db=db, event_bus=event_bus, tool_registry=tool_registry)
        CommandRegistry.instance().register(cmd)
        return cmd


_OWL_META = CommandMeta(
    grammar="verb",
    group="Owls",
    subcommands=(
        SubCommand(
            name="create",
            summary="Create an owl (free text or flags) — elicits anything missing",
            description=(
                "Describe the owl in plain language, or pass flags "
                "(--name --preset|--explicit_tools --specialty --schedule --goal "
                "--lifecycle --boundaries --evolution_strategy). Missing required "
                "fields are asked for interactively, the same way chat creation works."
            ),
            args=(Arg(name="text_or_flags", summary="free-text description, or --flags"),),
            examples=(
                Example(invocation="/owl create a research assistant that reads arxiv daily"),
                Example(invocation='/owl create --name Sage --preset researcher --schedule "every 2h"'),
            ),
        ),
        SubCommand(
            name="edit",
            summary="Update fields on an owl you created",
            args=(Arg(name="name", summary="owl name"),),
            examples=(Example(invocation='/owl edit Sage --preset writer'),),
        ),
        SubCommand(
            name="rename",
            summary="Change an owl's display name (cosmetic)",
            args=(Arg(name="name", summary="owl name"), Arg(name="display_name", summary="new label")),
            examples=(Example(invocation='/owl rename Sage "Sage the Scholar"'),),
        ),
        SubCommand(name="pause", summary="Suspend a scheduled owl's cadence",
                   args=(Arg(name="name", summary="owl name"),)),
        SubCommand(name="resume", summary="Resume a paused owl's cadence",
                   args=(Arg(name="name", summary="owl name"),)),
        SubCommand(name="retire", summary="Remove an owl you created",
                   args=(Arg(name="name", summary="owl name"),)),
        SubCommand(name="list", summary="Show your assistants"),
        SubCommand(name="dna", summary="Show DNA traits, current versus authored",
                   args=(Arg(name="name", summary="owl name"),)),
        SubCommand(
            name="reset-dna",
            summary="Revert evolved DNA to the authored baseline",
            description="You discard accumulated evolution and restore the owl's authored DNA. Confirmed with YES.",
            args=(Arg(name="name", summary="owl name"),),
            examples=(
                Example(invocation="/owl reset-dna Sage YES", note="Confirm reset"),
            ),
        ),
        SubCommand(name="health", summary="Report owl registry health"),
        SubCommand(name="objectives", summary="List standing objectives and their progress"),
        SubCommand(
            name="objective",
            summary="Show one objective's steps and activity log",
            args=(Arg(name="objective_id", summary="objective id"),),
            examples=(
                Example(invocation="/owl objective obj-1a2b3c4d"),
            ),
        ),
        SubCommand(
            name="objective-cancel",
            summary="Abandon a standing objective",
            description="You stop the assistant from pursuing an objective. Confirmed with YES.",
            args=(Arg(name="objective_id", summary="objective id"),),
            examples=(
                Example(invocation="/owl objective-cancel obj-1a2b3c4d YES", note="Confirm"),
            ),
        ),
    ),
)


class OwlCommand(OwlsCommand):
    """``/owl`` — the ONE owl surface. Every mutation (create/edit/rename/pause/
    resume/retire) funnels through the single owl_build engine so there is exactly
    one path from user intent to persisted owl (killing the /owls add-vs-create
    divergence). Inspection (list/dna/reset-dna/health/objectives) reuses the
    inherited registry-backed handlers unchanged."""

    @property
    def command(self) -> str:
        return "owl"

    @property
    def description(self) -> str:
        return "Manage your owls: create, edit, rename, pause, resume, retire, list, dna."

    @property
    def meta(self) -> CommandMeta:
        return _OWL_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        log.gateway.debug(
            "[commands] owl.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "menu":
                return await self._menu(rest)
            if sub == "create":
                return await self._build("create", parse_owl_build_flags(rest), state)
            if sub == "edit":
                name, flag_rest = _split_name(rest)
                return await self._build("edit", {"name": name, **parse_owl_build_flags(flag_rest)}, state)
            if sub == "rename":
                name, display = _two_positional(rest)
                return await self._build("rename", {"name": name, "display_name": display}, state)
            if sub in ("pause", "resume", "retire", "remove"):
                name, _ = _split_name(rest)
                action = "retire" if sub in ("retire", "remove") else sub
                return await self._build(action, {"name": name}, state)
            if sub == "list":
                return self._list()
            if sub == "dna":
                return await self._dna(rest)
            if sub == "reset-dna":
                return await self._reset_dna(rest)
            if sub == "health":
                return await self._health()
            if sub == "objectives":
                return await self._objectives()
            if sub == "objective":
                return await self._objective(rest)
            if sub == "objective-cancel":
                return await self._objective_cancel(rest)
            log.gateway.debug("[commands] owl.handle: unknown subcommand",
                              extra={"_fields": {"sub": sub}})
            return render_usage("owl", _OWL_META)
        except CommandParseError as exc:
            log.gateway.warning("[commands] owl.handle: parse error",
                                extra={"_fields": {"sub": sub, "error": str(exc)}})
            return f"✗ {exc}\n\n{render_usage('owl', _OWL_META)}"
        except (ManifestValidationError, OwlNotFoundError) as exc:
            log.gateway.warning("[commands] owl.handle: domain error",
                                extra={"_fields": {"sub": sub, "error": str(exc)}})
            return f"✗ /owl {sub}: {exc}"
        except Exception as exc:
            log.gateway.error("[commands] owl.handle: subcommand crashed",
                              exc_info=exc, extra={"_fields": {"sub": sub}})
            return f"✗ /owl {sub}: {exc}"

    async def _build(self, action: str, kwargs: dict[str, Any], state: PipelineState) -> str:
        """Route one /owl mutation through owl_build — the ONE mutation engine.

        Wraps the call in an interactive TraceContext (as /owls create already
        does) so owl_build's consent gate + elicitation can reach the user."""
        log.gateway.debug("[commands] owl._build: entry",
                          extra={"_fields": {"action": action, "keys": sorted(kwargs)}})
        # Lazy import — owl_build imports OwlsCommand at module top, so a top-level
        # import here is circular; also keeps OwlBuildTool monkeypatchable at origin.
        from stackowl.tools.meta.owl_build import OwlBuildTool

        token = TraceContext.start(
            session_id=state.session_id,
            trace_id=state.trace_id,
            interactive=True,
            channel=state.channel,
            reply_target=state.reply_target,
        )
        # Slash-command dispatch never runs the LLM pipeline backend, which is
        # the ONLY other place that populates get_services() (asyncio_backend /
        # langgraph_backend). Without this, OwlBuildTool's registry/db_pool
        # reads (e.g. _toggle_schedule's pause/resume) always see an empty
        # StepServices() and fail closed with "scheduling unavailable" even
        # though this command was constructed with real registry/db deps.
        svc_token = set_services(StepServices(owl_registry=self._registry, db_pool=self._db))
        try:
            result = await OwlBuildTool().execute(action=action, **kwargs)
        finally:
            reset_services(svc_token)
            TraceContext.reset(token)
        log.gateway.info("[commands] owl._build: exit",
                        extra={"_fields": {"action": action, "success": result.success}})
        return result.output if result.success else f"✗ /owl {action}: {result.error}"

    # -- menu (per-owl drill-down: pause/resume + retire) -----------------------

    async def _menu(self, raw: str) -> str | CommandResponse:
        log.gateway.debug("[commands] owl.menu: entry", extra={"_fields": {"raw_len": len(raw)}})
        name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not name:
            return "Usage: /owl menu <name>"
        if self._registry is None:
            return _NO_REGISTRY
        manifest = self._registry.get(name)  # raises OwlNotFoundError → handled in handle()
        does = (manifest.role or "").strip() or "general help"
        status = "🟢 active" if manifest.lifecycle == "scheduled" else "💤 resting"
        text = (
            f"{manifest.display} ({manifest.name}) — {does} · {status}\n"
            f"tier={manifest.model_tier}\n"
            f"Rename: /owl rename {name} <new_display_name>\n"
            f"Edit role: /owl edit {name} --role <new_role>"
        )
        actions: list[Action] = [
            Action(
                label=f"Set tier: {t}",
                command=f"/owl edit {name} --tier {t}",
                destructive=False,
            )
            for t in ("fast", "standard", "powerful", "local")
            if t != manifest.model_tier
        ]
        if manifest.lifecycle == "scheduled":
            paused = await self._is_job_paused(name)
            if paused is not None:
                verb = "resume" if paused else "pause"
                actions.append(
                    Action(label=verb.capitalize(), command=f"/owl {verb} {name}", destructive=False)
                )
        actions.append(Action(label=f"Retire {name}", command=f"/owl retire {name}", destructive=True))
        log.gateway.debug(
            "[commands] owl.menu: exit", extra={"_fields": {"name": name, "n_actions": len(actions)}}
        )
        return CommandResponse(text=text, actions=tuple(actions))

    async def _is_job_paused(self, name: str) -> bool | None:
        """True if the owl's scheduled job is paused, False if active, ``None``
        if undeterminable (no db wired, or no projected job row yet)."""
        if self._db is None:
            return None
        from stackowl.scheduler.owl_lifecycle import _job_id_for

        rows = await self._db.fetch_all(
            "SELECT enabled FROM jobs WHERE job_id = ?", (_job_id_for(name),)
        )
        if not rows:
            return None
        return not bool(rows[0].get("enabled", 1))


def _split_name(rest: str) -> tuple[str, str]:
    """Split ``<name> <remainder>`` — name is the first whitespace token."""
    stripped = rest.strip()
    if not stripped:
        raise CommandParseError("owl", "missing owl name")
    parts = stripped.split(maxsplit=1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _two_positional(rest: str) -> tuple[str, str]:
    """Parse ``<name> <display_name>`` (display_name may be quoted)."""
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise CommandParseError("owl", f"could not tokenise arguments: {exc}") from exc
    if len(tokens) < 2:
        raise CommandParseError("owl", "usage: /owl rename <name> <display_name>")
    return tokens[0], " ".join(tokens[1:])
