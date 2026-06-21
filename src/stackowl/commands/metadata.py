"""Structured sub-command metadata — the single source of truth for a command's
sub-commands, their help, usage, and (eventually) dispatch.

Design (party-designed, see plan): a command exposes a recursive tree of
:class:`SubCommand` nodes via :attr:`SlashCommand.meta`.  The SAME tree feeds:

* the terminal autocomplete dropdown (one-line ``summary`` per node),
* ``/help <command>`` and ``/help <command> <sub>`` (full ``description`` +
  ``args`` + ``examples``),
* auto-generated usage text (``render_usage``), shown on a wrong/empty
  sub-command instead of a hand-maintained ``_USAGE`` string that drifts.

The ``grammar`` discriminant is the honesty guarantee: ``"verb"`` commands offer
selectable sub-command rows; ``"flag"``/``"leaf"`` commands take positional
args/flags and MUST NOT advertise fake sub-commands (e.g. ``/connect <service>``
— the service is an operand, not a sub-command in the dispatch path).

The tree is recursive (``children``) so the 2-level ``/browser profile list``
case and any future N-level case use the SAME code — never special-cased.

Writing rules for authors (enforced by the schema lint, WS-quality):

* ``summary`` — verb-first, ≤60 chars, no trailing period, sentence case, and
  never repeats the command/sub name.  This is the load-bearing field: it must
  read correctly in a ~50-column dropdown.
* ``description`` — optional; full sentences ending in a period; second person
  ("you"); explains *why/when*, not a restatement of ``summary``.
* ``Arg.summary`` — ≤50 chars, no period, a noun phrase.
* ``Example.invocation`` — a real paste-able line including the leading slash.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.pipeline.state import PipelineState

# A sub-command handler receives the residual argument tail *after* the
# framework has peeled off the matched command/sub-command path.
SubHandler = Callable[[str, "PipelineState"], Awaitable[str]]

Grammar = Literal["verb", "flag", "leaf"]


@dataclass(frozen=True)
class Arg:
    """One positional argument or flag of a command/sub-command."""

    name: str
    required: bool = True
    repeat: bool = False
    summary: str = ""
    choices: tuple[str, ...] = ()

    def render(self) -> str:
        """Render the signature token: ``<name>`` / ``[name]`` / ``<name...>`` /
        ``<name: a|b>``."""
        inner = self.name
        if self.choices:
            inner = f"{self.name}: {'|'.join(self.choices)}"
        if self.repeat:
            inner = f"{inner}..."
        return f"<{inner}>" if self.required else f"[{inner}]"


@dataclass(frozen=True)
class Example:
    """A concrete, paste-able invocation plus an optional one-line note."""

    invocation: str
    note: str = ""


@dataclass(frozen=True)
class SubCommand:
    """One node in a command's sub-command tree.

    ``handler is None`` means the legacy ``handle()`` if/elif ladder still owns
    dispatch for this node (transition state); a populated ``handler`` lets the
    framework route directly (drive-mode), making metadata↔dispatch drift
    impossible for that node.
    """

    name: str
    summary: str
    description: str = ""
    args: tuple[Arg, ...] = ()
    examples: tuple[Example, ...] = ()
    aliases: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()
    handler: SubHandler | None = None
    children: tuple[SubCommand, ...] = ()

    def arg_signature(self) -> str:
        """Space-joined rendered args, e.g. ``<query> [--scope chat|global]``."""
        return " ".join(a.render() for a in self.args)


@dataclass(frozen=True)
class CommandMeta:
    """Top-level metadata attached to a :class:`SlashCommand`.

    ``grammar``:
        * ``"verb"`` — has ``subcommands``; autocomplete offers them.
        * ``"flag"`` — takes flags/positional ``args`` (e.g. ``/quiet``,
          ``/connect``); autocomplete shows only a non-selectable arg hint.
        * ``"leaf"`` — takes no structured args (e.g. ``/whoami``).
    """

    grammar: Grammar = "verb"
    subcommands: tuple[SubCommand, ...] = ()
    args: tuple[Arg, ...] = ()
    examples: tuple[Example, ...] = ()
    # Where in /help the command is grouped (index headers). Optional.
    group: str = ""

    def is_empty(self) -> bool:
        """True when no metadata has been declared (the ABC default).

        Used to detect un-migrated commands so they keep their legacy behaviour
        byte-for-byte.
        """
        return self.grammar == "verb" and not self.subcommands and not self.args


def resolve_path(
    subcommands: tuple[SubCommand, ...], path: Sequence[str]
) -> SubCommand | None:
    """Walk ``subcommands`` following ``path`` tokens; return the node or ``None``.

    Matches on ``name`` or any alias. Recurses into ``children`` for each
    remaining token, so 2-level and N-level lookups share one implementation.
    """
    if not path:
        return None
    head, *rest = path
    head_cf = head.casefold()
    for sub in subcommands:
        if head_cf == sub.name.casefold() or head_cf in {a.casefold() for a in sub.aliases}:
            if not rest:
                return sub
            return resolve_path(sub.children, rest)
    return None


def render_usage(command: str, meta: CommandMeta) -> str:
    """Auto-generate the usage block for a command from its metadata.

    Replaces hand-maintained ``_USAGE`` constants. For ``verb`` grammar it lists
    the (first level of) sub-commands with their summaries; for ``flag``/``leaf``
    it renders the arg signature. One level deep — the full tree lives in
    ``/help`` (progressive disclosure).
    """
    lines: list[str] = []
    if meta.grammar == "verb" and meta.subcommands:
        lines.append(f"Usage: /{command} <subcommand> [args]")
        lines.append("")
        lines.append("Subcommands:")
        width = max((len(_sub_label(s)) for s in meta.subcommands), default=0)
        for sub in meta.subcommands:
            marker = " ›" if sub.children else ""
            lines.append(f"  {_sub_label(sub):<{width}}  {sub.summary}{marker}")
    else:
        sig = " ".join(a.render() for a in meta.args)
        lines.append(f"Usage: /{command} {sig}".rstrip())
        for arg in meta.args:
            if arg.summary:
                lines.append(f"  {arg.render():<24} {arg.summary}")
    return "\n".join(lines)


def _sub_label(sub: SubCommand) -> str:
    """``name`` plus its arg signature, e.g. ``search <query>``."""
    sig = sub.arg_signature()
    return f"{sub.name} {sig}".rstrip()
