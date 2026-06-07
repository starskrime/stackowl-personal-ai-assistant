"""OwlsCommand — /owls slash command for owl persona management.

Subcommands: ``list``, ``add``, ``remove``, ``health``, ``dna``.

The command takes its dependencies via constructor injection so the wiring
layer can decide whether to give it a real :class:`OwlRegistry`, a real
:class:`DbPool` and a real :class:`EventBus`, or ``None`` in test mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import config_path, load_yaml, save_yaml
from stackowl.commands.owls_helpers import (
    build_owl_manifest,
    format_dna_display,
    format_owl_table,
    manifest_to_yaml_entry,
    parse_add_args,
    parse_edit_args,
)
from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import (
    CommandParseError,
    ManifestValidationError,
    OwlNotFoundError,
)
from stackowl.infra.observability import log
from stackowl.owls.registry import _SECRETARY_NAME

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.state import PipelineState
    from stackowl.tools.registry import ToolRegistry

_USAGE = (
    "Usage: /owls <list|add|edit|remove|health|dna> [args]\n"
    "  /owls list                              — show registered owls\n"
    "  /owls add <name> --role <r> --tier <t>  — register a new owl\n"
    "  /owls edit <name> [--tier <t> ...]      — update fields on an existing owl\n"
    "  /owls remove <name>                     — start removal (asks for YES)\n"
    "  /owls health                            — report registry health\n"
    "  /owls dna <name>                        — show DNA traits"
)

_NO_REGISTRY = "(no owl registry wired — start StackOwl normally to manage owls)"
_NO_DB = "(no database wired — DNA state is not yet persisted)"

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
        return "Manage owl personas: list, add, remove, health, dna."

    async def handle(self, args: str, state: PipelineState) -> str:
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
            elif sub == "add":
                result = await self._add(rest)
            elif sub == "edit":
                result = await self._edit(rest)
            elif sub == "remove":
                result = await self._remove(rest)
            elif sub == "health":
                result = await self._health()
            elif sub == "dna":
                result = await self._dna(rest)
            else:
                log.gateway.debug(
                    "[commands] owls.handle: unknown subcommand",
                    extra={"_fields": {"sub": sub}},
                )
                return _USAGE
        except CommandParseError as exc:
            log.gateway.warning(
                "[commands] owls.handle: parse error",
                extra={"_fields": {"sub": sub, "error": str(exc)}},
            )
            return f"✗ {exc}\n\n{_USAGE}"
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
    def _list(self) -> str:
        log.gateway.debug("[commands] owls.list: entry")
        if self._registry is None:
            return _NO_REGISTRY
        result = format_owl_table(self._registry.list())
        log.gateway.debug("[commands] owls.list: exit")
        return result

    # ------------------------------------------------------------------- add
    async def _add(self, rest: str) -> str:
        log.gateway.debug(
            "[commands] owls.add: entry",
            extra={"_fields": {"rest_len": len(rest)}},
        )
        if self._registry is None:
            return _NO_REGISTRY
        params = parse_add_args(rest)
        valid = (
            frozenset(t.name for t in self._tool_registry.all())
            if self._tool_registry is not None else None
        )
        manifest = build_owl_manifest(params, valid_tools=valid)
        manifest = manifest.model_copy(update={"origin": "human"})
        self._registry.register(manifest)  # may raise ManifestValidationError
        if self._db is not None:
            from stackowl.owls.dna_authored import capture_one_authored

            await capture_one_authored(self._db, manifest.name, manifest.dna)
        self._upsert_to_yaml(manifest_to_yaml_entry(manifest))
        if self._bus is not None:
            self._bus.emit("owl_added", {"name": manifest.name, "role": manifest.role})
        log.gateway.info(
            "[commands] owls.add: exit",
            extra={"_fields": {"name": manifest.name, "role": manifest.role}},
        )
        return f"✓ owl '{manifest.name}' registered (role={manifest.role}, tier={manifest.model_tier})"

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
        return f"✓ owl '{updated.name}' updated (tier={updated.model_tier})"

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
        if self._bus is not None:
            self._bus.emit("owl_removed", {"name": name})
        log.gateway.info(
            "[commands] owls.remove: exit",
            extra={"_fields": {"name": name}},
        )
        return f"✓ owl '{name}' removed"

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
        result = format_dna_display(name, manifest.dna, db_row)
        log.gateway.debug(
            "[commands] owls.dna: exit",
            extra={"_fields": {"name": name, "has_db_row": db_row is not None}},
        )
        return result

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
