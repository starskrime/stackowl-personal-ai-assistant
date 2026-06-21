"""Three-rung /help renderers — pure functions over command metadata.

Progressive disclosure for a terminal (Paige's IA):

* ``render_index``       — "what commands exist"        (/help)
* ``render_command_page``— "what can THIS command do"    (/help <command>)
* ``render_subcommand_page`` — "exactly how do I call this" (/help <command> <sub>)

Each rung names the next at its footer, so the user descends one named level at
a time and never leaves the terminal.  Everything is composed from the SAME
``CommandMeta`` that drives dispatch and autocomplete — there is no second copy
to drift.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import SubCommand

_DEFAULT_GROUP = "Other"


def render_index(commands: list[SlashCommand]) -> str:
    """Rung 1 — grouped command index. ``▸`` marks a command with sub-commands."""
    if not commands:
        return "(no commands registered)"

    groups: dict[str, list[SlashCommand]] = {}
    for cmd in commands:
        group = cmd.meta.group or _DEFAULT_GROUP
        groups.setdefault(group, []).append(cmd)

    # Stable: named groups alphabetically, the catch-all last.
    ordered = sorted(g for g in groups if g != _DEFAULT_GROUP)
    if _DEFAULT_GROUP in groups:
        ordered.append(_DEFAULT_GROUP)

    width = max(len(c.command) for c in commands) + 2  # room for the ▸ marker
    lines: list[str] = ["Available commands:", ""]
    for group in ordered:
        lines.append(group)
        for cmd in sorted(groups[group], key=lambda c: c.command):
            marker = " ▸" if cmd.meta.subcommands else ""
            label = f"/{cmd.command}{marker}"
            lines.append(f"  {label:<{width}}  {cmd.description}")
        lines.append("")
    lines.append("▸ has sub-commands.  Type  /help <command>  to go deeper.")
    return "\n".join(lines)


def render_command_page(cmd: SlashCommand) -> str:
    """Rung 2 — one command's page: description, usage, one level of children."""
    name = cmd.command
    meta = cmd.meta
    lines: list[str] = [f"/{name} — {cmd.description}", ""]

    if meta.grammar == "verb" and meta.subcommands:
        lines.append(f"USAGE\n  /{name} <subcommand> [args]")
        lines.append("")
        lines.append("SUBCOMMANDS")
        labels = [_child_label(s) for s in meta.subcommands]
        col = max((len(label) for label in labels), default=0)
        for sub, label in zip(meta.subcommands, labels, strict=True):
            marker = " ▸" if sub.children else ""
            lines.append(f"  {label:<{col}}  {sub.summary}{marker}")
        lines.extend(_examples_block(meta.examples))
        lines.append("")
        lines.append(f"Type  /help {name} <subcommand>  for one.")
    else:
        sig = " ".join(a.render() for a in meta.args)
        lines.append(f"USAGE\n  /{name} {sig}".rstrip())
        lines.extend(_arguments_block(meta.args))
        lines.extend(_examples_block(meta.examples))
    return "\n".join(lines)


def render_subcommand_page(command: str, path: list[str], node: SubCommand) -> str:
    """Rung 3 — a leaf (or branch) sub-command page: full args, examples, see-also.

    If ``node`` itself has children (a branch like ``browser profile``), its
    children are listed one level deep, mirroring the command page.
    """
    full = f"/{command} {' '.join(path)}".rstrip()
    lines: list[str] = [f"{full} — {node.summary}"]
    if node.aliases:
        lines[0] += f"   [aliases: {', '.join(node.aliases)}]"
    lines.append("")
    if node.description:
        lines.append(node.description)
        lines.append("")

    sig = node.arg_signature()
    lines.append(f"USAGE\n  {full} {sig}".rstrip())

    if node.children:
        lines.append("")
        lines.append("SUBCOMMANDS")
        labels = [_child_label(c) for c in node.children]
        col = max((len(label) for label in labels), default=0)
        for child, label in zip(node.children, labels, strict=True):
            lines.append(f"  {label:<{col}}  {child.summary}")

    lines.extend(_arguments_block(node.args))
    lines.extend(_examples_block(node.examples))
    if node.see_also:
        lines.append("")
        lines.append(f"See also: {', '.join(node.see_also)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared blocks
# ---------------------------------------------------------------------------


def _child_label(sub: SubCommand) -> str:
    sig = sub.arg_signature()
    return f"{sub.name} {sig}".rstrip()


def _arguments_block(args: tuple) -> list[str]:  # type: ignore[type-arg]
    documented = [a for a in args if a.summary]
    if not documented:
        return []
    out = ["", "ARGUMENTS"]
    col = max(len(a.render()) for a in documented)
    for arg in documented:
        out.append(f"  {arg.render():<{col}}  {arg.summary}")
    return out


def _examples_block(examples: tuple) -> list[str]:  # type: ignore[type-arg]
    if not examples:
        return []
    out = ["", "EXAMPLES"]
    col = max(len(e.invocation) for e in examples)
    for ex in examples:
        note = f"  {ex.note}" if ex.note else ""
        out.append(f"  {ex.invocation:<{col}}{note}")
    return out
