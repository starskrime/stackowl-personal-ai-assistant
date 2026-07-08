"""PreferencesCommand — ``/preferences`` slash command (FR-2).

Lists the durable, aspect-scoped preference NOTES captured from confident
content/tone/length feedback (``pipeline/steps/feedback.py`` writes them via
``memory.preferences.write_preference_note``) and lets the user remove one by
its displayed number. Read/manage only — this command never WRITES a note
itself; writing happens exclusively via the classifier-verdict-driven capture
path in ``feedback.py`` (never keyword-matched here), matching the PRD's
"no keyword matching" out-of-scope note for FR-2.

Follows ``style_command.py``'s shape: same ``preference_store`` constructor
dep, same owner_key resolution (``state.identity_key or state.session_id`` —
mirrors the delivery seam's scope key exactly).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, SubCommand, render_usage
from stackowl.commands.response import Action, CommandResponse
from stackowl.infra.observability import log
from stackowl.memory.preferences import PREFERENCE_NOTES_KEY, load_preference_notes

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.pipeline.state import PipelineState

_PREFERENCES_META = CommandMeta(
    grammar="verb",
    group="Output & Formatting",
    subcommands=(
        SubCommand(
            name="list",
            summary="Show learned content/tone/length preference notes",
        ),
        SubCommand(
            name="remove",
            summary="Remove one preference note by its number",
            args=(Arg(name="n", summary="note number from /preferences list"),),
        ),
    ),
)


class PreferencesCommand(SlashCommand):
    """``/preferences`` — list/remove learned content/tone/length preference notes."""

    def __init__(self, preference_store: PreferenceStore | None = None) -> None:
        self._store = preference_store

    @property
    def command(self) -> str:
        return "preferences"

    @property
    def description(self) -> str:
        return "List or remove learned content/tone/length preference notes."

    @property
    def meta(self) -> CommandMeta:
        return _PREFERENCES_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        # 1. ENTRY
        log.gateway.debug(
            "[commands] preferences.handle: entry",
            extra={"_fields": {"session": state.session_id, "args_len": len(args)}},
        )
        if self._store is None:
            return "/preferences: not configured (no preference store)."
        owner_key = state.identity_key or state.session_id
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        # 2. DECISION — dispatch on subcommand
        if sub == "remove":
            arg = parts[1].strip() if len(parts) > 1 else ""
            if not arg.isdigit():
                return "Usage: /preferences remove <n>\n\n" + render_usage(
                    "preferences", _PREFERENCES_META
                )
            return await self._remove(owner_key, int(arg))
        if sub not in ("list", ""):
            return f"preferences: unknown subcommand {sub!r}\n\n" + render_usage(
                "preferences", _PREFERENCES_META
            )
        return await self._list(owner_key)

    async def _list(self, owner_key: str) -> str | CommandResponse:
        assert self._store is not None  # narrowed by handle() guard
        # 3. STEP
        notes = await load_preference_notes(self._store, owner_key)
        if not notes:
            log.gateway.info(
                "[commands] preferences.list: exit — none set",
                extra={"_fields": {"owner_key": owner_key}},
            )
            return "No learned preference notes yet."
        lines = ["Learned preference notes:"]
        actions: list[Action] = []
        for i, n in enumerate(notes, start=1):
            text = str(n.get("text", ""))
            lines.append(f"  {i}. [{n.get('aspect')}/{n.get('polarity')}] {text}")
            preview = text if len(text) <= 40 else text[:37] + "..."
            actions.append(
                Action(
                    label=f"Remove: {preview}",
                    command=f"/preferences remove {i}",
                    destructive=True,
                )
            )
        lines.append("Remove one with /preferences remove <n>.")
        # 4. EXIT
        log.gateway.info(
            "[commands] preferences.list: exit",
            extra={"_fields": {"owner_key": owner_key, "n": len(notes)}},
        )
        return CommandResponse(text="\n".join(lines), actions=tuple(actions))

    async def _remove(self, owner_key: str, n: int) -> str:
        assert self._store is not None  # narrowed by handle() guard
        # 3. STEP
        notes = await load_preference_notes(self._store, owner_key)
        if n < 1 or n > len(notes):
            log.gateway.info(
                "[commands] preferences.remove: exit — out of range",
                extra={"_fields": {"owner_key": owner_key, "n": n, "count": len(notes)}},
            )
            return f"No preference note #{n}. Use /preferences list to see current notes."
        removed = notes.pop(n - 1)
        await self._store.set(owner_key, PREFERENCE_NOTES_KEY, json.dumps(notes))
        # 4. EXIT
        log.gateway.info(
            "[commands] preferences.remove: exit",
            extra={"_fields": {"owner_key": owner_key, "removed_aspect": removed.get("aspect")}},
        )
        return f"Removed preference note #{n} ({removed.get('aspect')})."
