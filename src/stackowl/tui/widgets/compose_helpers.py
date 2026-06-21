"""Helpers for :class:`ComposeArea` — autocomplete filtering pure logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from stackowl.commands.metadata import CommandMeta, resolve_path

if TYPE_CHECKING:
    from stackowl.commands.metadata import SubCommand

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
    # Ensure the @ is at start or follows whitespace — avoids matching an
    # email-like token mid-line.
    if at_idx >= 0 and (at_idx == 0 or value[at_idx - 1].isspace()):
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
    """A slash command's name + one-line description (for the dropdown).

    ``meta`` carries the command's :class:`CommandMeta` (grammar + sub-command
    tree) so the dropdown can offer context-aware sub-command completion without
    re-reaching into the live registry on every keystroke.  Sub-commands are
    STATIC class metadata, so a one-shot startup snapshot (orchestrator) is
    sufficient and authoritative — see :func:`parse_completion`.  Defaults to an
    empty :class:`CommandMeta` so any caller that omits it keeps name-only
    behaviour byte-for-byte.
    """

    name: str
    description: str
    meta: CommandMeta = field(default_factory=CommandMeta)


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


# ---------------------------------------------------------------------------
# Context-aware sub-command completion (deterministic; no AI)
# ---------------------------------------------------------------------------


class CompletionLevel(Enum):
    """What the buffer is asking us to complete right now."""

    NONE = "none"
    COMMAND = "command"
    SUB = "sub"


@dataclass(frozen=True)
class SubCandidate:
    """One selectable sub-command row.

    ``has_children`` drives the trailing ``›`` marker in the dropdown and the
    "insert a trailing space so the user can descend further" behaviour on
    selection.  ``has_args`` is true when the node takes positional args/flags,
    which likewise warrants a trailing space after insertion.
    """

    name: str
    summary: str
    has_children: bool = False
    has_args: bool = False


@dataclass(frozen=True)
class CompletionContext:
    """A pure parse of the current input buffer for autocomplete.

    * ``level`` — COMMAND (filter top-level names), SUB (filter sub-commands of
      a settled verb command), or NONE (nothing to complete: past args, a
      flag/leaf-grammar command, or an unknown command).
    * ``partial`` — the trailing token currently being typed (``""`` right after
      a space).
    * ``command`` — the settled top-level command name (SUB level only).
    * ``candidates`` — sub-command rows (SUB level); empty otherwise. Top-level
      command filtering still goes through :func:`filter_command_infos`, so this
      tuple is only populated at SUB level.
    * ``settled_verb`` — when the buffer is exactly ``/<verbcmd>`` (no trailing
      space) and ``<verbcmd>`` is a known ``grammar="verb"`` command, its name;
      lets the caller descend a level on Tab. ``None`` otherwise.
    """

    level: CompletionLevel
    partial: str = ""
    command: str | None = None
    candidates: tuple[SubCandidate, ...] = ()
    settled_verb: str | None = None


def _sub_candidates(
    subs: tuple[SubCommand, ...], partial: str
) -> tuple[SubCandidate, ...]:
    """Prefix-filter ``subs`` by ``partial`` (case-insensitive), preserving order."""
    needle = partial.casefold()
    out: list[SubCandidate] = []
    for sub in subs:
        if needle and not sub.name.casefold().startswith(needle):
            continue
        out.append(
            SubCandidate(
                name=sub.name,
                summary=sub.summary,
                has_children=bool(sub.children),
                has_args=bool(sub.args),
            )
        )
    return tuple(out)


def parse_completion(
    buffer: str, infos: list[CommandInfo]
) -> CompletionContext:
    """Decide what (if anything) the buffer is completing — pure, no I/O.

    Walks the already-typed sub tokens with :func:`resolve_path`, so 2-level
    (``/browser profile ``) and any future N-level case share one code path —
    NO command is special-cased.  Honesty: ``grammar != "verb"`` commands never
    offer selectable sub-command rows (their operands are args, not
    sub-commands).
    """
    if not buffer.startswith("/"):
        return CompletionContext(level=CompletionLevel.NONE)

    # Split off the leading slash. ``str.split`` collapses runs of spaces, but
    # we need to know whether the buffer ENDS in a space (→ a fresh, empty token
    # is being started) — track that separately.
    body = buffer[1:]
    ends_with_space = body.endswith(" ")
    tokens = body.split()

    # `/` or `/me` → still choosing the top-level command.
    if not tokens or (len(tokens) == 1 and not ends_with_space):
        partial = tokens[0] if tokens else ""
        settled_verb = _exact_verb(partial, infos)
        return CompletionContext(
            level=CompletionLevel.COMMAND,
            partial=partial,
            settled_verb=settled_verb,
        )

    # First token is a settled command name; look it up.
    cmd_name = tokens[0]
    info = _find_info(cmd_name, infos)
    if info is None or info.meta.grammar != "verb":
        # Unknown command, or a flag/leaf command: no selectable sub rows.
        return CompletionContext(level=CompletionLevel.NONE, command=cmd_name)

    # Remaining tokens after the command name. The last one is the partial
    # being typed UNLESS the buffer ends in a space (then partial == "").
    rest = tokens[1:]
    if ends_with_space:
        path = rest
        partial = ""
    else:
        path = rest[:-1]
        partial = rest[-1]

    if not path:
        # Directly under the command: complete its first-level sub-commands.
        return CompletionContext(
            level=CompletionLevel.SUB,
            partial=partial,
            command=cmd_name,
            candidates=_sub_candidates(info.meta.subcommands, partial),
        )

    # Descend the already-typed sub tokens. resolve_path is N-level + alias-aware.
    node = resolve_path(info.meta.subcommands, path)
    if node is None or not node.children:
        # Past the navigable tree (e.g. `/memory search foo `, or a terminal
        # leaf followed by a space): nothing left to complete.
        return CompletionContext(level=CompletionLevel.NONE, command=cmd_name)

    return CompletionContext(
        level=CompletionLevel.SUB,
        partial=partial,
        command=cmd_name,
        candidates=_sub_candidates(node.children, partial),
    )


def command_dropdown_items(
    value: str, infos: list[CommandInfo]
) -> tuple[CompletionLevel, tuple[tuple[str, str | None], ...]]:
    """Resolve the dropdown rows for a slash buffer — the single decision point.

    Returns ``(level, items)`` where ``items`` is the ``(name, description)``
    list the :class:`AutocompleteDropdown` renders:

    * COMMAND level → top-level commands prefix-filtered by the partial, each
      carrying its one-line ``description`` (unchanged legacy behaviour).
    * SUB level → the settled command's sub-command rows, each carrying its
      ``summary`` and a trailing ``›`` marker when the node has children.
    * NONE → empty items (caller hides the dropdown).

    Keeping this one function authoritative means the widget never re-derives the
    command-vs-sub decision — it just renders what this returns.
    """
    ctx = parse_completion(value, infos)
    if ctx.level is CompletionLevel.COMMAND:
        filtered = filter_command_infos(ctx.partial, infos)
        items = tuple((ci.name, ci.description) for ci in filtered)
        return (CompletionLevel.COMMAND, items)
    if ctx.level is CompletionLevel.SUB:
        rows: list[tuple[str, str | None]] = []
        for cand in ctx.candidates:
            marker = " ›" if cand.has_children else ""
            label = f"{cand.summary}{marker}" if cand.summary else (marker.strip() or None)
            rows.append((cand.name, label))
        return (CompletionLevel.SUB, tuple(rows))
    return (CompletionLevel.NONE, ())


def _find_info(name: str, infos: list[CommandInfo]) -> CommandInfo | None:
    """Case-insensitive exact lookup of a CommandInfo by name."""
    needle = name.casefold()
    for info in infos:
        if info.name.casefold() == needle:
            return info
    return None


def _exact_verb(name: str, infos: list[CommandInfo]) -> str | None:
    """Return ``name`` when it exactly matches a known verb command, else None."""
    if not name:
        return None
    info = _find_info(name, infos)
    if info is not None and info.meta.grammar == "verb" and info.meta.subcommands:
        return info.name
    return None
