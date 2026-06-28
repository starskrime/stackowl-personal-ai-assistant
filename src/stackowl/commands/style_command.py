"""StyleCommand — ``/style`` slash command (LS5).

Shows the user the ACTIVE output style for their current channel in PLAIN
language, so they can SEE that a stated format preference is durable without
re-asking ("you only tell me once"). Read-only: it reuses ``load_output_style``
(the SAME scope-merging resolver the LS2 delivery seam enforces) and
``OutputStyle.describe_rules`` (the SAME plain-language wording the LS4 feedback
confirmation reads back), so what ``/style`` shows can never drift from what is
actually enforced on the next reply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.channels._format import load_output_style
from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import CommandMeta
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.pipeline.state import PipelineState

_STYLE_META = CommandMeta(grammar="leaf", group="Output & Formatting")


class StyleCommand(SlashCommand):
    """Show the active enforced output style for the current channel."""

    def __init__(self, preference_store: PreferenceStore | None = None) -> None:
        self._store = preference_store

    @property
    def command(self) -> str:
        return "style"

    @property
    def description(self) -> str:
        return "Show the active output formatting style for this channel."

    @property
    def meta(self) -> CommandMeta:
        return _STYLE_META

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "[commands] style.handle: entry",
            extra={"_fields": {"session": state.session_id, "channel": state.channel}},
        )
        if self._store is None:
            return "/style: not configured (no preference store)."
        # Mirror the delivery seam's scope key exactly: cross-channel identity when
        # resolved, else the per-channel session — so what /style reports is the
        # same record the next reply is enforced against.
        owner_key = state.identity_key or state.session_id
        style = await load_output_style(self._store, owner_key)
        rules = style.describe_rules()
        channel = (state.channel or "this channel").capitalize()
        if not rules:
            log.gateway.info(
                "[commands] style.handle: exit — no custom style set",
                extra={"_fields": {"owner_key": owner_key, "channel": state.channel}},
            )
            return (
                f"No custom output style set for {channel} yet — replies use the "
                "default formatting. Tell me how you'd like output formatted "
                "(for example: no asterisks, or links shown as titles) and I'll "
                "keep to it."
            )
        log.gateway.info(
            "[commands] style.handle: exit — style active",
            extra={"_fields": {"owner_key": owner_key, "rules": rules}},
        )
        return (
            f"Your {channel} style (active): {' · '.join(rules)}. "
            "Tap /style anytime to check it; tell me a change to update it."
        )
