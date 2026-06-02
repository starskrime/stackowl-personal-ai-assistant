"""ComposeArea — bottom input area with slash & at-mention autocomplete."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static, TextArea

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize
from stackowl.tui.messages import (
    AutocompleteSelectedMessage,
    ComposeAreaStateMessage,
    ComposeSubmittedMessage,
)
from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_helpers import (
    AutocompleteKind,
    AutocompleteState,
    CommandInfo,
    build_state,
    filter_candidates,
    filter_command_infos,
)
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

if TYPE_CHECKING:
    from collections.abc import Iterable

    from textual.app import ComposeResult

_INPUT_ID = "compose_input"
_PLACEHOLDER_ID = "compose_placeholder"
_DROPDOWN_ID = "autocomplete_dropdown"

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
        dock: bottom;
        height: 5;
        min-height: 3;
        background: $color-surface;
        border-top: solid $color-border;
        layers: editor overlay;
    }
    ComposeArea SubmitTextArea {
        layer: editor;
        height: 1fr;
        background: $color-bg-elevated;
        color: $color-text-primary;
    }
    ComposeArea .compose-placeholder {
        layer: overlay;
        offset: 1 0;
        width: 1fr;
        height: auto;
        color: $color-text-muted;
        background: transparent;
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
        self._dropdown_open: bool = False

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
        # TextArea has no placeholder property — render a dim overlay Static that
        # is shown only while the editor is empty.
        yield SubmitTextArea(id=_INPUT_ID)
        yield Static(
            localize("compose.placeholder"),
            id=_PLACEHOLDER_ID,
            classes="compose-placeholder",
        )
        yield AutocompleteDropdown(id=_DROPDOWN_ID)

    def on_mount(self) -> None:
        """Wire the editor's nav hook to the dropdown router; start hidden."""
        log.tui.debug(
            "[tui] compose_area.on_mount: entry",
            extra={"_fields": {}},
        )
        try:
            editor = self.query_one(f"#{_INPUT_ID}", SubmitTextArea)
            editor.nav_hook = self._handle_autocomplete_key
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.on_mount: editor not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
        self._hide_autocomplete()
        log.tui.debug(
            "[tui] compose_area.on_mount: exit",
            extra={"_fields": {"dropdown_open": self._dropdown_open}},
        )

    # ------------------------------------------------------------------ reactive
    def watch_state(self, new_state: str) -> None:
        log.tui.debug(
            "[tui] compose_area.watch_state: decision",
            extra={"_fields": {"state": new_state}},
        )

    # ------------------------------------------------------------------ events
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        value = event.text_area.text
        log.tui.debug(
            "[tui] compose_area.on_text_area_changed: entry",
            extra={"_fields": {"len": len(value)}},
        )
        # Empty editor → show the placeholder overlay; otherwise hide it.
        self._set_placeholder_visible(not value)
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

    def on_submit_text_area_submitted(
        self, event: SubmitTextArea.Submitted
    ) -> None:
        log.tui.debug(
            "[tui] compose_area.on_submit_text_area_submitted: entry",
            extra={"_fields": {"state": self.state}},
        )
        if self.state == _STATE_MCP_DISABLED:
            log.tui.warning(
                "[tui] compose_area.on_submit_text_area_submitted: blocked — mcp disabled",
                extra={"_fields": {}},
            )
            return
        text = event.text.strip()
        if not text:
            return
        self.state = _STATE_SUBMITTING
        self.post_message(ComposeSubmittedMessage(text=text))
        try:
            editor = self.query_one(f"#{_INPUT_ID}", SubmitTextArea)
            editor.text = ""
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.on_submit_text_area_submitted: failed to clear editor",
                exc_info=exc,
                extra={"_fields": {}},
            )
        self._set_placeholder_visible(True)
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
            editor = self.query_one(f"#{_INPUT_ID}", SubmitTextArea)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.set_mcp_disabled: editor not mounted",
                exc_info=exc,
                extra={"_fields": {"disabled": disabled}},
            )
            editor = None
        if disabled:
            self.state = _STATE_MCP_DISABLED
            self._set_placeholder_text(localize("compose.mcp_disabled"))
            if editor is not None:
                editor.disabled = True
            return
        self.state = _STATE_IDLE
        self._set_placeholder_text(localize("compose.placeholder"))
        if editor is not None:
            editor.disabled = False

    def set_parliament_active(self, active: bool) -> None:
        """Swap placeholder hint when a parliament session is running."""
        log.tui.debug(
            "[tui] compose_area.set_parliament_active: entry",
            extra={"_fields": {"active": active}},
        )
        key = "compose.parliament_active" if active else "compose.placeholder"
        self._set_placeholder_text(localize(key))

    # ------------------------------------------------------------------ placeholder
    def _set_placeholder_visible(self, visible: bool) -> None:
        """Show/hide the overlay placeholder Static. Self-healing if unmounted."""
        log.tui.debug(
            "[tui] compose_area._set_placeholder_visible: step",
            extra={"_fields": {"visible": visible}},
        )
        try:
            placeholder = self.query_one(f"#{_PLACEHOLDER_ID}", Static)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._set_placeholder_visible: placeholder not mounted",
                exc_info=exc,
                extra={"_fields": {"visible": visible}},
            )
            return
        placeholder.display = visible

    def _set_placeholder_text(self, text: str) -> None:
        """Swap the overlay placeholder's content (TextArea has no placeholder)."""
        log.tui.debug(
            "[tui] compose_area._set_placeholder_text: step",
            extra={"_fields": {"len": len(text)}},
        )
        try:
            placeholder = self.query_one(f"#{_PLACEHOLDER_ID}", Static)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._set_placeholder_text: placeholder not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        placeholder.update(text)

    # ------------------------------------------------------------------ autocomplete
    def _dropdown(self) -> AutocompleteDropdown | None:
        """Self-healing accessor for the dropdown overlay."""
        try:
            return self.query_one(f"#{_DROPDOWN_ID}", AutocompleteDropdown)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._dropdown: not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return None

    def _show_command_autocomplete(self, prefix: str) -> None:
        # Use filter_command_infos (carries descriptions) against the same
        # prefix the name-only build_state already computed.
        infos = filter_command_infos(prefix, self._command_infos)
        log.tui.debug(
            "[tui] compose_area._show_command_autocomplete: step",
            extra={"_fields": {"prefix_len": len(prefix), "candidates": len(infos)}},
        )
        if not infos:
            self._hide_autocomplete()
            return
        items: list[tuple[str, str | None]] = [
            (ci.name, ci.description) for ci in infos
        ]
        dropdown = self._dropdown()
        if dropdown is None:
            return
        dropdown.set_items(items)
        dropdown.display = True
        self._dropdown_open = True

    def _show_owl_autocomplete(self, prefix: str) -> None:
        names = filter_candidates(prefix, self._owl_names)
        log.tui.debug(
            "[tui] compose_area._show_owl_autocomplete: step",
            extra={"_fields": {"prefix_len": len(prefix), "candidates": len(names)}},
        )
        if not names:
            self._hide_autocomplete()
            return
        items: list[tuple[str, str | None]] = [(n, None) for n in names]
        dropdown = self._dropdown()
        if dropdown is None:
            return
        dropdown.set_items(items)
        dropdown.display = True
        self._dropdown_open = True

    def _hide_autocomplete(self) -> None:
        log.tui.debug(
            "[tui] compose_area._hide_autocomplete: step",
            extra={"_fields": {}},
        )
        self._dropdown_open = False
        dropdown = self._dropdown()
        if dropdown is not None:
            dropdown.display = False

    def _handle_autocomplete_key(self, key: str) -> bool:
        """Nav-hook router — drive the dropdown while it is open.

        Returns ``True`` iff the key was consumed by the dropdown (so the editor
        must NOT process it).  When the dropdown is closed, or for keys the
        dropdown does not own, returns ``False`` so the editor handles the key
        normally (and ``on_text_area_changed`` refreshes the candidate list).
        """
        if not self._dropdown_open:
            return False
        dropdown = self._dropdown()
        if dropdown is None:
            self._dropdown_open = False
            return False
        log.tui.debug(
            "[tui] compose_area._handle_autocomplete_key: decision",
            extra={"_fields": {"key": key}},
        )
        if key == "down":
            dropdown.move_down()
            return True
        if key == "up":
            dropdown.move_up()
            return True
        if key in ("tab", "enter"):
            self._complete_current()
            return True
        if key == "escape":
            self._hide_autocomplete()
            return True
        return False

    def _complete_current(self) -> None:
        """Accept the highlighted candidate: rewrite editor text + post message."""
        dropdown = self._dropdown()
        if dropdown is None:
            return
        name = dropdown.current()
        if name is None:
            return
        is_command = self._autocomplete_state.kind == AutocompleteKind.COMMAND
        completion_type = "command" if is_command else "owl"
        log.tui.debug(
            "[tui] compose_area._complete_current: entry",
            extra={"_fields": {"name": name, "type": completion_type}},
        )
        try:
            editor = self.query_one(f"#{_INPUT_ID}", SubmitTextArea)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._complete_current: editor not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        if is_command:
            # Command trigger is always at line start ("/prefix") → replace wholesale.
            new_text = f"/{name} "
        else:
            # Replace the last "@<prefix>" token with "@<name> ".
            text = editor.text
            at_idx = text.rfind("@")
            new_text = f"@{name} " if at_idx < 0 else f"{text[:at_idx]}@{name} "
        editor.text = new_text
        editor.move_cursor(editor.document.end)
        self._set_placeholder_visible(not new_text)
        self.post_message(
            AutocompleteSelectedMessage(selected=name, completion_type=completion_type)
        )
        self._hide_autocomplete()
        log.tui.debug(
            "[tui] compose_area._complete_current: exit",
            extra={"_fields": {"text_len": len(new_text), "type": completion_type}},
        )

    # ------------------------------------------------------------------ actions
    def action_clear_input(self) -> None:
        """Binding action — wipe the compose input without submitting."""
        log.tui.debug(
            "[tui] compose_area.action_clear_input: entry",
            extra={"_fields": {"state": self.state}},
        )
        try:
            editor = self.query_one(f"#{_INPUT_ID}", SubmitTextArea)
            editor.text = ""
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.action_clear_input: editor not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        self._set_placeholder_visible(True)
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
