"""ComposeArea — bottom input area with slash & at-mention autocomplete."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize
from stackowl.tui.messages import ComposeAreaStateMessage, ComposeSubmittedMessage
from stackowl.tui.widgets.compose_helpers import (
    AutocompleteKind,
    AutocompleteState,
    CommandInfo,
    build_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from textual.app import ComposeResult

_INPUT_ID = "compose_input"

_STATE_IDLE = "idle"
_STATE_COMPOSING = "composing"
_STATE_SUBMITTING = "submitting"
_STATE_MCP_DISABLED = "mcp-disabled"


class ComposeArea(Widget):
    """Bottom input area.

    Detects ``/`` and ``@`` triggers, runs a pure-function autocomplete
    builder against optional command/owl name suppliers, and posts a
    :class:`ComposeSubmittedMessage` on Enter.  When an MCP spectator is
    connected the input is disabled wholesale.
    """

    DEFAULT_CSS = """
    ComposeArea {
        height: 3;
        background: $color-surface;
        border-top: solid $color-border;
    }
    ComposeArea Input {
        background: $color-bg-elevated;
        color: $color-text-primary;
    }
    """

    BINDINGS = [
        Binding("ctrl+l", "clear_input", "Clear input"),
        Binding("escape", "cancel_autocomplete", "Cancel autocomplete"),
    ]

    state: reactive[str] = reactive(_STATE_IDLE)

    def __init__(
        self,
        *,
        command_names: Iterable[str] | None = None,
        command_infos: Iterable[CommandInfo] | None = None,
        owl_names: Iterable[str] | None = None,
    ) -> None:
        super().__init__()
        infos: list[CommandInfo] = list(command_infos or [])
        self._command_infos: list[CommandInfo] = infos
        self._desc_by_name: dict[str, str] = {ci.name: ci.description for ci in infos}
        self._command_names: list[str] = list(command_names or [])
        # Passing only command_infos still powers name autocomplete; an
        # explicit command_names wins for back-compat.
        if command_names is None and infos:
            self._command_names = [ci.name for ci in infos]
        log.tui.debug(
            "[tui] compose_area.__init__: entry",
            extra={"_fields": {
                "command_count": len(self._command_names),
                "desc_count": len(self._desc_by_name),
            }},
        )
        self._owl_names: list[str] = list(owl_names or [])
        self._autocomplete_state: AutocompleteState = AutocompleteState(
            kind=AutocompleteKind.NONE, prefix="", candidates=()
        )

    # ------------------------------------------------------------------ binding
    def set_command_names(self, names: Iterable[str]) -> None:
        """Replace the command-name registry snapshot."""
        log.tui.debug(
            "[tui] compose_area.set_command_names: entry",
            extra={"_fields": {"count": len(list(names))}},
        )
        self._command_names = list(names)

    def set_owl_names(self, names: Iterable[str]) -> None:
        """Replace the owl-name registry snapshot."""
        log.tui.debug(
            "[tui] compose_area.set_owl_names: entry",
            extra={"_fields": {"count": len(list(names))}},
        )
        self._owl_names = list(names)

    # ------------------------------------------------------------------ access
    @property
    def autocomplete_state(self) -> AutocompleteState:
        """Last computed autocomplete snapshot — read-only test surface."""
        return self._autocomplete_state

    # ------------------------------------------------------------------ compose
    def compose(self) -> ComposeResult:
        yield Input(id=_INPUT_ID, placeholder=localize("compose.placeholder"))

    # ------------------------------------------------------------------ reactive
    def watch_state(self, new_state: str) -> None:
        log.tui.debug(
            "[tui] compose_area.watch_state: decision",
            extra={"_fields": {"state": new_state}},
        )

    # ------------------------------------------------------------------ events
    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        log.tui.debug(
            "[tui] compose_area.on_input_changed: entry",
            extra={"_fields": {"len": len(value)}},
        )
        if self.state == _STATE_MCP_DISABLED:
            return
        if value:
            self.state = _STATE_COMPOSING
        else:
            self.state = _STATE_IDLE
        self._autocomplete_state = build_state(
            value=value,
            command_names=self._command_names,
            owl_names=self._owl_names,
        )
        if self._autocomplete_state.kind == AutocompleteKind.COMMAND:
            self._show_command_autocomplete(self._autocomplete_state.prefix)
        elif self._autocomplete_state.kind == AutocompleteKind.OWL:
            self._show_owl_autocomplete(self._autocomplete_state.prefix)
        else:
            self._hide_autocomplete()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        log.tui.debug(
            "[tui] compose_area.on_input_submitted: entry",
            extra={"_fields": {"state": self.state}},
        )
        if self.state == _STATE_MCP_DISABLED:
            log.tui.warning(
                "[tui] compose_area.on_input_submitted: blocked — mcp disabled",
                extra={"_fields": {}},
            )
            return
        text = event.value.strip()
        if not text:
            return
        self.state = _STATE_SUBMITTING
        self.post_message(ComposeSubmittedMessage(text=text))
        try:
            input_widget = self.query_one(f"#{_INPUT_ID}", Input)
            input_widget.value = ""
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.on_input_submitted: failed to clear input",
                exc_info=exc,
                extra={"_fields": {}},
            )
        self.state = _STATE_IDLE

    # ------------------------------------------------------------------ state
    def on_compose_area_state_message(self, message: ComposeAreaStateMessage) -> None:
        """Apply an externally-pushed compose-area state (e.g. MCP spectator lock)."""
        log.tui.debug(
            "[tui] compose_area.on_compose_area_state_message: entry",
            extra={"_fields": {"state": message.state}},
        )
        self.set_mcp_disabled(message.state == _STATE_MCP_DISABLED)
        log.tui.debug(
            "[tui] compose_area.on_compose_area_state_message: exit",
            extra={"_fields": {"state": self.state}},
        )

    def set_mcp_disabled(self, disabled: bool) -> None:
        """Toggle the MCP-disabled lockout state and update placeholder text."""
        log.tui.debug(
            "[tui] compose_area.set_mcp_disabled: entry",
            extra={"_fields": {"disabled": disabled}},
        )
        try:
            input_widget = self.query_one(f"#{_INPUT_ID}", Input)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.set_mcp_disabled: input not mounted",
                exc_info=exc,
                extra={"_fields": {"disabled": disabled}},
            )
            input_widget = None
        if disabled:
            self.state = _STATE_MCP_DISABLED
            if input_widget is not None:
                input_widget.placeholder = localize("compose.mcp_disabled")
                input_widget.disabled = True
            return
        self.state = _STATE_IDLE
        if input_widget is not None:
            input_widget.placeholder = localize("compose.placeholder")
            input_widget.disabled = False

    def set_parliament_active(self, active: bool) -> None:
        """Swap placeholder hint when a parliament session is running."""
        log.tui.debug(
            "[tui] compose_area.set_parliament_active: entry",
            extra={"_fields": {"active": active}},
        )
        try:
            input_widget = self.query_one(f"#{_INPUT_ID}", Input)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.set_parliament_active: input not mounted",
                exc_info=exc,
                extra={"_fields": {"active": active}},
            )
            return
        key = "compose.parliament_active" if active else "compose.placeholder"
        input_widget.placeholder = localize(key)

    # ------------------------------------------------------------------ autocomplete
    def _show_command_autocomplete(self, prefix: str) -> None:
        log.tui.debug(
            "[tui] compose_area._show_command_autocomplete: step",
            extra={
                "_fields": {
                    "prefix_len": len(prefix),
                    "candidates": len(self._autocomplete_state.candidates),
                }
            },
        )

    def _show_owl_autocomplete(self, prefix: str) -> None:
        log.tui.debug(
            "[tui] compose_area._show_owl_autocomplete: step",
            extra={
                "_fields": {
                    "prefix_len": len(prefix),
                    "candidates": len(self._autocomplete_state.candidates),
                }
            },
        )

    def _hide_autocomplete(self) -> None:
        log.tui.debug(
            "[tui] compose_area._hide_autocomplete: step",
            extra={"_fields": {}},
        )

    # ------------------------------------------------------------------ actions
    def action_clear_input(self) -> None:
        """Binding action — wipe the compose input without submitting."""
        log.tui.debug(
            "[tui] compose_area.action_clear_input: entry",
            extra={"_fields": {"state": self.state}},
        )
        try:
            input_widget = self.query_one(f"#{_INPUT_ID}", Input)
            input_widget.value = ""
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.action_clear_input: input not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        self.state = _STATE_IDLE
        self._hide_autocomplete()

    def action_cancel_autocomplete(self) -> None:
        """Binding action — dismiss any open autocomplete dropdown."""
        log.tui.debug(
            "[tui] compose_area.action_cancel_autocomplete: entry",
            extra={"_fields": {}},
        )
        self._autocomplete_state = AutocompleteState(
            kind=AutocompleteKind.NONE, prefix="", candidates=()
        )
        self._hide_autocomplete()
