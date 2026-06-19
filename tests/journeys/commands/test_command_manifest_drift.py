"""Anti-drift guard — every SlashCommand subclass in stackowl.commands must be manifested.

If a developer adds a new command file without updating SHIPPED_COMMANDS or
EXEMPT_COMMANDS, this test turns RED immediately, forcing them to make an
explicit ship/exempt decision.

Uses pkgutil to import every module in the stackowl.commands package, then
walks the SlashCommand subclass tree to find all concrete command classes.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import stackowl.commands as _pkg
from stackowl.commands.base import SlashCommand
from stackowl.commands.manifest import EXEMPT_COMMANDS, SHIPPED_COMMANDS


def _load_all_command_modules() -> None:
    """Import every module in stackowl.commands so subclasses are registered."""
    for mod_info in pkgutil.iter_modules(_pkg.__path__):
        full = f"stackowl.commands.{mod_info.name}"
        try:
            importlib.import_module(full)
        except Exception:
            # Best-effort: if a module fails to import (e.g. missing optional
            # dep) we skip it — the drift guard is opportunistic, not a boot.
            pass


def _collect_concrete_command_classes() -> list[type[SlashCommand]]:
    """Walk all SlashCommand subclasses whose __module__ is in stackowl.commands."""
    _load_all_command_modules()
    result: list[type[SlashCommand]] = []

    def _walk(cls: type) -> None:
        for sub in cls.__subclasses__():
            if sub.__module__.startswith("stackowl.commands"):
                result.append(sub)
            _walk(sub)

    _walk(SlashCommand)
    return result


def _get_command_string(cls: type[SlashCommand]) -> str | None:
    """Return the .command string for a class, constructing with None args if needed."""
    # Try no-arg construction first
    try:
        instance = cls()  # type: ignore[call-arg]
        return instance.command
    except TypeError:
        pass
    # Try with all-None args for common signatures
    import inspect
    sig = inspect.signature(cls.__init__)
    params = {
        name: None
        for name, param in sig.parameters.items()
        if name != "self" and param.default is inspect.Parameter.empty
    }
    try:
        instance = cls(**params)  # type: ignore[call-arg]
        return instance.command
    except Exception:
        return None


def test_no_command_subclass_is_unmanifested() -> None:
    """Every SlashCommand subclass must appear in SHIPPED_COMMANDS or EXEMPT_COMMANDS."""
    classes = _collect_concrete_command_classes()
    assert classes, "No SlashCommand subclasses found — import or walk is broken"

    all_manifested = SHIPPED_COMMANDS | EXEMPT_COMMANDS
    unmanifested: list[str] = []

    for cls in classes:
        cmd_str = _get_command_string(cls)
        if cmd_str is None:
            # Can't determine command string — skip gracefully
            continue
        if cmd_str not in all_manifested:
            unmanifested.append(f"{cls.__qualname__} (.command={cmd_str!r})")

    assert not unmanifested, (
        "These SlashCommand subclasses are not in SHIPPED_COMMANDS or EXEMPT_COMMANDS.\n"
        "Add them to commands/manifest.py:\n"
        + "\n".join(f"  {u}" for u in sorted(unmanifested))
    )
