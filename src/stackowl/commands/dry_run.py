"""Dry-run preview — ``/command ... ??`` shows what would happen, runs nothing.

A trailing ``??`` on any slash command is intercepted at the dispatch chokepoint
(``CommandRegistry.dispatch``) BEFORE the handler is called, so the preview is
honest by construction: no handler runs, nothing is committed, the give-up floor
can never be tripped.  The preview is composed purely from the command's
metadata (the resolved sub-command's ``summary``/``description`` + the args that
would be passed), so it needs no per-command dry-run support.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.metadata import SubCommand, resolve_path

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.commands.base import SlashCommand

DRY_RUN_SIGIL = "??"


def strip_sigil(args: str) -> tuple[bool, str]:
    """Detect and remove a trailing ``??`` dry-run sigil.

    Returns ``(is_dry_run, cleaned_args)``.  The sigil is only recognised as a
    trailing token so it can't collide with a real argument value.
    """
    stripped = args.rstrip()
    if stripped.endswith(DRY_RUN_SIGIL):
        return True, stripped[: -len(DRY_RUN_SIGIL)].rstrip()
    return False, args


def build_preview(command: str, cmd: SlashCommand, cleaned_args: str) -> str:
    """Compose a no-side-effect preview of ``/command cleaned_args``."""
    tokens = cleaned_args.split()
    node: SubCommand | None = None
    path: list[str] = []
    rest: list[str] = tokens

    # Find the longest leading token run that resolves to a sub-command node;
    # the remainder are that node's arguments.
    for i in range(len(tokens), 0, -1):
        candidate = resolve_path(cmd.meta.subcommands, tokens[:i])
        if candidate is not None:
            node, path, rest = candidate, tokens[:i], tokens[i:]
            break

    invocation = "/" + command
    if path:
        invocation += " " + " ".join(path)
    shown = invocation + (" " + " ".join(rest) if rest else "")

    summary = node.summary if node is not None else cmd.description
    description = node.description if node is not None else ""

    lines = [f"Preview — {shown}   (nothing has run)"]
    if summary:
        lines.append(summary)
    if description:
        lines.append(description)
    if rest:
        lines.append("")
        lines.append(f"Arguments: {' '.join(rest)}")
    lines.append("")
    lines.append("This is a dry run. Remove the trailing ?? to actually run it.")
    return "\n".join(lines)
