"""Helpers for :class:`ComposeArea` — autocomplete filtering pure logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Default candidate cap. Must comfortably exceed the full shipped slash-command
# surface (~29) so an empty "/" prefix lists EVERY command rather than the first
# handful — the dropdown scrolls the highlight into view (see AutocompleteDropdown),
# so a large list stays navigable. Kept finite as a guard against a pathological
# owl roster.
_DEFAULT_LIMIT = 100


class AutocompleteKind(Enum):
    """Discriminator for the kind of completion being shown."""

    NONE = "none"
    COMMAND = "command"
    OWL = "owl"


@dataclass(frozen=True)
class AutocompleteState:
    """Snapshot of what the dropdown should display right now.

    Plain immutable record so the widget can compute a fresh state on every
    input change without sharing mutable state with the renderer.
    """

    kind: AutocompleteKind
    prefix: str
    candidates: tuple[str, ...]


def detect_kind(value: str) -> tuple[AutocompleteKind, str]:
    """Detect which autocomplete (if any) applies to the current input.

    Args:
        value: Current input value.

    Returns:
        ``(kind, prefix)`` — ``prefix`` is the substring after the trigger
        character.  ``NONE`` when no trigger applies; the prefix is then ``""``.
    """
    if value.startswith("/"):
        return (AutocompleteKind.COMMAND, value[1:])
    at_idx = value.rfind("@")
    if at_idx >= 0:
        # Ensure the @ is at start or follows whitespace — avoids matching
        # an email-like token mid-line.
        if at_idx == 0 or value[at_idx - 1].isspace():
            return (AutocompleteKind.OWL, value[at_idx + 1 :])
    return (AutocompleteKind.NONE, "")


def filter_candidates(
    prefix: str, names: list[str], limit: int = _DEFAULT_LIMIT
) -> tuple[str, ...]:
    """Return up to ``limit`` candidates whose name starts with ``prefix``.

    Case-insensitive, Unicode-safe (relies on ``str.casefold``).  Preserves
    the input order of ``names`` for deterministic display.
    """
    if limit <= 0:
        return ()
    needle = prefix.casefold()
    if not needle:
        return tuple(names[:limit])
    matches = [n for n in names if n.casefold().startswith(needle)]
    return tuple(matches[:limit])


@dataclass(frozen=True)
class CommandInfo:
    """A slash command's name + one-line description (for the dropdown)."""

    name: str
    description: str


def filter_command_infos(
    prefix: str, infos: list[CommandInfo], limit: int = _DEFAULT_LIMIT
) -> tuple[CommandInfo, ...]:
    """Up to ``limit`` CommandInfos whose ``name`` starts with ``prefix``.

    Case-insensitive (``str.casefold``), Unicode-safe, preserves input order —
    mirrors :func:`filter_candidates` but carries descriptions through.
    """
    if limit <= 0:
        return ()
    needle = prefix.casefold()
    if not needle:
        return tuple(infos[:limit])
    matches = [ci for ci in infos if ci.name.casefold().startswith(needle)]
    return tuple(matches[:limit])


def build_state(
    value: str,
    command_names: list[str],
    owl_names: list[str],
) -> AutocompleteState:
    """Build a fresh :class:`AutocompleteState` from the current input."""
    kind, prefix = detect_kind(value)
    if kind == AutocompleteKind.COMMAND:
        return AutocompleteState(
            kind=kind,
            prefix=prefix,
            candidates=filter_candidates(prefix, command_names),
        )
    if kind == AutocompleteKind.OWL:
        return AutocompleteState(
            kind=kind,
            prefix=prefix,
            candidates=filter_candidates(prefix, owl_names),
        )
    return AutocompleteState(kind=AutocompleteKind.NONE, prefix="", candidates=())
