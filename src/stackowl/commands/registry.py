"""CommandRegistry — singleton dispatch table for all slash commands."""

from __future__ import annotations

from collections.abc import Sequence

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import SubCommand, resolve_path
from stackowl.exceptions import CommandNotFoundError
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState


class CommandRegistry:
    """Singleton registry; open for extension — plugins call register() at import time."""

    _instance: CommandRegistry | None = None

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._source_map: dict[str, list[str]] = {}

    @classmethod
    def instance(cls) -> CommandRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — test use only."""
        cls._instance = None

    def register(self, command: SlashCommand, source_name: str | None = None) -> None:
        self._commands[command.command] = command
        if source_name:
            self._source_map.setdefault(source_name, []).append(command.command)
        log.gateway.debug(
            "[commands] registry.register: command registered",
            extra={"_fields": {"command": command.command, "source": source_name}},
        )

    def unregister_by_source(self, source_name: str) -> int:
        """Remove all commands registered under source_name. Returns count removed."""
        log.gateway.debug(
            "[commands] registry.unregister_by_source: entry",
            extra={"_fields": {"source": source_name}},
        )
        names = self._source_map.pop(source_name, [])
        for name in names:
            self._commands.pop(name, None)
        log.gateway.debug(
            "[commands] registry.unregister_by_source: exit",
            extra={"_fields": {"source": source_name, "removed": len(names)}},
        )
        return len(names)

    async def dispatch(self, name: str, args: str, state: PipelineState) -> str:
        if name not in self._commands:
            raise CommandNotFoundError(name)
        # Log the LENGTH, never the raw args — a command's args can carry a
        # secret (e.g. `/provider add … token=…`) and the field-key redactor
        # can't scrub a secret embedded inside a value string. Mirrors the
        # CLI adapter, which logs text_len rather than the text.
        log.gateway.debug(
            "[commands] registry.dispatch: dispatching",
            extra={"_fields": {"command": name, "args_len": len(args)}},
        )
        return await self._commands[name].handle(args, state)

    def list(self) -> list[SlashCommand]:
        return sorted(self._commands.values(), key=lambda c: c.command)

    def get(self, name: str) -> SlashCommand | None:
        """Return the command registered under ``name`` (without '/'), or None."""
        return self._commands.get(name)

    def resolve(self, path: Sequence[str]) -> SubCommand | None:
        """Resolve a ``[command, sub, sub-sub, ...]`` path to a SubCommand node.

        The first token names the command; the remainder walks its sub-command
        tree.  Returns ``None`` if the command is unknown or the path does not
        match a declared node.  Used by both the parity contract test and (for
        migrated commands) drive-mode dispatch.
        """
        if not path:
            return None
        cmd = self._commands.get(path[0])
        if cmd is None:
            return None
        return resolve_path(cmd.meta.subcommands, path[1:])


def register_command(cmd: SlashCommand) -> SlashCommand:
    """Decorator-style helper: register a SlashCommand at import time."""
    CommandRegistry.instance().register(cmd)
    return cmd


def load_builtin_commands(registry: CommandRegistry | None = None) -> int:
    """Import every ``*_command.py`` module so they self-register.

    Parameters
    ----------
    registry:
        Target registry for Pattern-A (module-level ``_CMD``) instances.
        Defaults to ``CommandRegistry.instance()``.  Pass an explicit registry
        (e.g. from :func:`register_all_commands`) so that Pattern-A commands
        land in the same registry as Pattern-B DI commands — not the global
        singleton — when an isolated registry is requested.

    Returns the number of commands now in the target registry. Safe to call
    multiple times — re-importing modules is a no-op and ``register`` simply
    overwrites.

    After a :meth:`CommandRegistry.reset`, the module-level
    ``_CMD = register_command(...)`` lines do NOT re-execute (the modules are
    already in ``sys.modules``).  To handle that case this function also walks
    already-loaded ``*_command`` modules and re-registers any ``_CMD`` they
    expose — idempotent because ``register`` overwrites the same slot.

    Note: the import-time ``register_command(cmd)`` helper (called by
    module-level ``_CMD = register_command(...)`` at first import) still
    registers to the global singleton — that is correct for normal startup
    where the singleton IS the target.  This function then re-registers those
    same instances into *registry* so an explicit isolated registry also gets
    them.
    """
    import importlib
    import pkgutil
    import sys

    import stackowl.commands as pkg

    target = registry if registry is not None else CommandRegistry.instance()
    before = len(target.list())
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if not mod_info.name.endswith("_command"):
            continue
        full = f"stackowl.commands.{mod_info.name}"
        try:
            importlib.import_module(full)
        except Exception as exc:
            log.gateway.warning(
                "[commands] registry.load_builtin_commands: import failed",
                exc_info=exc,
                extra={"_fields": {"module": full}},
            )
    # Re-register any _CMD instances from already-cached modules into *target*
    # (handles post-reset() scenarios where import_module is a no-op, and
    # ensures Pattern-A commands land in an explicit isolated registry).
    from stackowl.commands.base import SlashCommand as _SlashCommand

    prefix = "stackowl.commands."
    for name, mod in list(sys.modules.items()):
        if not (name.startswith(prefix) and name[len(prefix):].endswith("_command")):
            continue
        cmd = getattr(mod, "_CMD", None)
        if isinstance(cmd, _SlashCommand):
            target.register(cmd)
    after = len(target.list())
    log.gateway.info(
        "[commands] registry.load_builtin_commands: discovered",
        extra={"_fields": {"before": before, "after": after, "added": after - before}},
    )
    return after
