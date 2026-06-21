"""ComposeArea — bottom input area with slash & at-mention autocomplete."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Static, TextArea

from stackowl.commands.metadata import resolve_path
from stackowl.infra.observability import log
from stackowl.tui.glyphs import GLYPH_PROMPT
from stackowl.tui.i18n import localize
from stackowl.tui.messages import (
    AutocompleteSelectedMessage,
    ComposeAreaStateMessage,
    ComposeSubmittedMessage,
)
from stackowl.tui.widgets.autocomplete_dropdown import AutocompleteDropdown
from stackowl.tui.widgets.compose_helpers import (
    ROW_SEMANTIC,
    ROW_SUGGESTED,
    AutocompleteKind,
    AutocompleteState,
    CommandInfo,
    CompletionLevel,
    build_state,
    command_dropdown_items,
    filter_candidates,
    is_path_prefix,
    mark_rows,
    parse_completion,
    predict_next_token,
)
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

if TYPE_CHECKING:
    from collections.abc import Iterable

    from textual.app import ComposeResult

    from stackowl.commands.resolver import CommandResolver
    from stackowl.commands.sequence_store import SequenceSuggestionProvider

_INPUT_ID = "compose_input"
_DROPDOWN_ID = "autocomplete_dropdown"
_ROW_ID = "compose_row"
_PROMPT_ID = "compose_prompt"
_HINT_ID = "compose_hint"

_MAX_INPUT_ROWS = 8

_STATE_IDLE = "idle"
_STATE_COMPOSING = "composing"
_STATE_SUBMITTING = "submitting"
_STATE_MCP_DISABLED = "mcp-disabled"


class ComposeArea(Vertical):
    """Bottom input zone — a minimal ``❯`` prompt over a thin rule.

    An auto-height vertical zone docked at the screen bottom. Top→bottom it
    stacks: the in-flow autocomplete palette (hidden until there are
    candidates), a prompt row (``❯`` + an auto-growing multiline editor) with a
    bottom rule, and a dim hint line that doubles as the MCP-disabled /
    parliament-active state indicator. Detects ``/`` and ``@`` triggers, runs a
    pure-function autocomplete builder, and posts a
    :class:`ComposeSubmittedMessage` on Enter.

    Subclasses :class:`~textual.containers.Vertical` so the children stack and
    the zone auto-sizes — a bare ``Widget`` collapses stacked children to zero.
    Keeping the palette IN-FLOW (rather than an offset overlay) is what makes it
    actually visible: an earlier overlay version was clipped by the parent.
    """

    DEFAULT_CSS = """
    ComposeArea {
        dock: bottom;
        height: auto;
        background: $color-surface;
        border-top: solid $color-border;
    }
    ComposeArea #compose_row {
        height: auto;
        border-bottom: solid $color-border;
    }
    ComposeArea #compose_prompt {
        width: 2;
        height: auto;
        color: $color-accent;
    }
    ComposeArea SubmitTextArea,
    ComposeArea SubmitTextArea:focus {
        width: 1fr;
        height: 1;
        max-height: 8;
        background: $color-surface;
        color: $color-text-primary;
        border: none;
        padding: 0;
    }
    ComposeArea #compose_hint {
        height: 1;
        color: $color-text-muted;
        padding: 0 1;
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
        sequence_provider: SequenceSuggestionProvider | None = None,
        semantic_resolver: CommandResolver | None = None,
    ) -> None:
        super().__init__()
        infos: list[CommandInfo] = list(command_infos or [])
        self._command_infos: list[CommandInfo] = infos
        # WS-D AI augmentation (both default None → byte-identical deterministic
        # dropdown). The provider feeds the ☆-suggested lane (bare "/"); the
        # resolver powers the prose semantic panel + is consulted nowhere else.
        self._sequence_provider = sequence_provider
        self._semantic_resolver = semantic_resolver
        # Forward ghost-text: the predicted suffix to append on Right-arrow.
        self._ghost_suffix: str = ""
        self._parliament_active: bool = False
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
        # Tracks whether the currently-shown command dropdown is at COMMAND
        # (top-level) or SUB (sub-command) level, so selection inserts the right
        # token and Tab can descend a level. NONE when no command dropdown.
        self._completion_level: CompletionLevel = CompletionLevel.NONE

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
        # In-flow palette (top), then the prompt row, then the hint line. The
        # palette is hidden until there are candidates; keeping it in flow (not
        # an offset overlay) means it is never clipped by this zone.
        yield AutocompleteDropdown(id=_DROPDOWN_ID)
        with Horizontal(id=_ROW_ID):
            yield Static(f"{GLYPH_PROMPT} ", id=_PROMPT_ID)
            yield SubmitTextArea(id=_INPUT_ID)
        yield Static(localize("compose.hints"), id=_HINT_ID)

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
        # Grow the editor with its content (compact when empty).
        self._autogrow(event.text_area)
        if self.state == _STATE_MCP_DISABLED:
            return
        if value:
            self.state = _STATE_COMPOSING
        else:
            self.state = _STATE_IDLE

        # Gait-read (WS-D): a non-empty natural-language phrase that is NOT a
        # command path switches the panel to resolver-ranked command candidates
        # — but only when the semantic layer is enabled. Off → fall through to
        # the deterministic path below (byte-identical legacy behaviour).
        if (
            self._semantic_resolver is not None
            and value.strip()
            and not is_path_prefix(value)
        ):
            self._clear_ghost()
            self.run_worker(
                self._show_semantic_panel(value),
                exclusive=True,
                group="ac_async",
            )
            return

        self._autocomplete_state = build_state(
            value=value,
            command_names=self._command_names,
            owl_names=self._owl_names,
        )
        if self._autocomplete_state.kind == AutocompleteKind.COMMAND:
            self._show_command_autocomplete(value)
        elif self._autocomplete_state.kind == AutocompleteKind.OWL:
            self._show_owl_autocomplete(self._autocomplete_state.prefix)
        else:
            self._hide_autocomplete()

        # Forward ghost-text prediction (deterministic; path mode only).
        self._update_ghost(value)

        # ☆ suggested lane — only in the low-commitment window (just "/" typed).
        # Collapses to zero the instant a narrowing keystroke makes value != "/".
        if self._sequence_provider is not None and value == "/":
            self.run_worker(
                self._load_suggested_lane(), exclusive=True, group="ac_async"
            )

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
            self._autogrow(editor)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.on_submit_text_area_submitted: failed to clear editor",
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
        """Toggle the MCP-disabled lockout and reflect it in the hint line."""
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
            self._set_hint_text(localize("compose.mcp_disabled"))
            if editor is not None:
                editor.disabled = True
            return
        self.state = _STATE_IDLE
        self._set_hint_text(localize("compose.hints"))
        if editor is not None:
            editor.disabled = False

    def set_parliament_active(self, active: bool) -> None:
        """Swap the hint line when a parliament session is running."""
        log.tui.debug(
            "[tui] compose_area.set_parliament_active: entry",
            extra={"_fields": {"active": active}},
        )
        self._parliament_active = active
        if active:
            self._clear_ghost()
        key = "compose.parliament_active" if active else "compose.hints"
        self._set_hint_text(localize(key))

    # ------------------------------------------------------------------ hint + autogrow
    def _set_hint_text(self, text: str) -> None:
        """Swap the hint line content (also the mcp/parliament state indicator)."""
        log.tui.debug(
            "[tui] compose_area._set_hint_text: step",
            extra={"_fields": {"len": len(text)}},
        )
        try:
            hint = self.query_one(f"#{_HINT_ID}", Static)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._set_hint_text: hint not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        hint.update(text)

    def _autogrow(self, editor: TextArea) -> None:
        """Size the editor to its content height, clamped to ``_MAX_INPUT_ROWS``."""
        try:
            lines = editor.document.line_count
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._autogrow: line count unavailable",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return
        rows = max(1, min(lines, _MAX_INPUT_ROWS))
        editor.styles.height = rows
        log.tui.debug(
            "[tui] compose_area._autogrow: step",
            extra={"_fields": {"lines": lines, "rows": rows}},
        )

    # ------------------------------------------------------------------ autocomplete
    def _dropdown(self) -> AutocompleteDropdown | None:
        """Self-healing accessor for the in-flow autocomplete palette."""
        try:
            return self.query_one(f"#{_DROPDOWN_ID}", AutocompleteDropdown)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._dropdown: not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return None

    def _show_command_autocomplete(self, value: str) -> None:
        # One authoritative decision: top-level command rows (with descriptions)
        # vs context-aware sub-command rows (with summaries + a ``›`` marker for
        # nodes that have children). flag/leaf grammar and past-args resolve to
        # NONE → no fake sub rows (honesty).
        level, items = command_dropdown_items(value, self._command_infos)
        log.tui.debug(
            "[tui] compose_area._show_command_autocomplete: step",
            extra={"_fields": {"level": level.value, "candidates": len(items)}},
        )
        if not items:
            self._completion_level = CompletionLevel.NONE
            self._hide_autocomplete()
            return
        self._completion_level = level
        dropdown = self._dropdown()
        if dropdown is None:
            return
        dropdown.set_items(list(items))
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
        self._completion_level = CompletionLevel.NONE
        items: list[tuple[str, str | None]] = [(n, None) for n in names]
        dropdown = self._dropdown()
        if dropdown is None:
            return
        dropdown.set_items(items)
        dropdown.display = True
        self._dropdown_open = True

    def _editor(self) -> SubmitTextArea | None:
        """Self-healing accessor for the compose editor."""
        try:
            return self.query_one(f"#{_INPUT_ID}", SubmitTextArea)
        except Exception:
            return None

    # ------------------------------------------------------------------ WS-D async lanes
    async def _load_suggested_lane(self) -> None:
        """Fetch learned next-likely commands and fence them above the "/" list.

        Runs only in the low-commitment window (the buffer is exactly "/"). If
        the user has typed anything more by the time the DB answers, it bails —
        the lane must collapse on the first narrowing keystroke. The suggested
        rows are ☆-marked, never the default highlight, and never auto-fire.
        """
        provider = self._sequence_provider
        if provider is None:
            return
        suggestions = await provider.suggest(limit=3)
        editor = self._editor()
        if editor is None or editor.text != "/" or not suggestions:
            return
        sug_rows = mark_rows(
            [(s.invocation, "you usually do this next") for s in suggestions],
            ROW_SUGGESTED,
        )
        _level, items = command_dropdown_items("/", self._command_infos)
        dropdown = self._dropdown()
        if dropdown is None:
            return
        dropdown.set_items([*sug_rows, *items])
        dropdown.display = True
        self._dropdown_open = True
        self._completion_level = CompletionLevel.COMMAND
        log.tui.debug(
            "[tui] compose_area._load_suggested_lane: shown",
            extra={"_fields": {"suggested": len(sug_rows)}},
        )

    async def _show_semantic_panel(self, value: str) -> None:
        """Rank command candidates for a natural-language phrase (resolver).

        The panel opens with NO row selected so Enter still SUBMITS the prose to
        the owl — the user must deliberately arrow/Tab into a candidate, which
        only POPULATES the box (never fires). Bails if the buffer changed while
        the resolver ran.
        """
        resolver = self._semantic_resolver
        if resolver is None:
            return
        candidates = await resolver.resolve(value, limit=6)
        editor = self._editor()
        if editor is None or editor.text != value:
            return  # the user typed on — this result is stale
        self._autocomplete_state = AutocompleteState(
            kind=AutocompleteKind.NONE, prefix="", candidates=()
        )
        if not candidates:
            self._hide_autocomplete()
            return
        rows = mark_rows(
            [(c.invocation, c.summary) for c in candidates], ROW_SEMANTIC
        )
        dropdown = self._dropdown()
        if dropdown is None:
            return
        dropdown.set_items(list(rows), allow_no_selection=True)
        dropdown.display = True
        self._dropdown_open = True
        self._completion_level = CompletionLevel.NONE
        log.tui.debug(
            "[tui] compose_area._show_semantic_panel: shown",
            extra={"_fields": {"candidates": len(rows)}},
        )

    # ------------------------------------------------------------------ ghost text
    def _update_ghost(self, value: str) -> None:
        """Recompute the forward ghost-text prediction and reflect it in the hint."""
        if self.state == _STATE_MCP_DISABLED or self._parliament_active:
            return
        suffix = predict_next_token(value, self._command_infos) or ""
        self._ghost_suffix = suffix
        if suffix:
            self._set_hint_text(f"→ {value}{suffix}     ·     → to accept")
        else:
            self._restore_hint()

    def _clear_ghost(self) -> None:
        """Drop any pending ghost-text and restore the default hint."""
        self._ghost_suffix = ""
        self._restore_hint()

    def _restore_hint(self) -> None:
        """Reset the hint line to its default (unless a state owns it)."""
        if self.state == _STATE_MCP_DISABLED or self._parliament_active:
            return
        self._set_hint_text(localize("compose.hints"))

    def _accept_ghost(self) -> bool:
        """Append the predicted ghost suffix to the buffer (Right-arrow)."""
        if not self._ghost_suffix:
            return False
        editor = self._editor()
        if editor is None or not self._cursor_at_end(editor):
            return False
        new_text = f"{editor.text}{self._ghost_suffix}"
        self._ghost_suffix = ""
        editor.text = new_text
        editor.move_cursor(editor.document.end)
        self._autogrow(editor)
        # Programmatic edits do not fire on_text_area_changed — recompute.
        self._show_command_autocomplete(new_text)
        self._update_ghost(new_text)
        return True

    def _cursor_at_end(self, editor: SubmitTextArea) -> bool:
        try:
            return editor.cursor_location == editor.document.end
        except Exception:
            return True

    def _hide_autocomplete(self) -> None:
        log.tui.debug(
            "[tui] compose_area._hide_autocomplete: step",
            extra={"_fields": {}},
        )
        self._dropdown_open = False
        self._completion_level = CompletionLevel.NONE
        dropdown = self._dropdown()
        if dropdown is not None:
            # Clear stale rows AND hide. Collapsing this in-flow palette leaves
            # the conversation region it covered un-invalidated on a real
            # terminal's incremental renderer (ghost text), so force a repaint.
            dropdown.set_items([])
            dropdown.display = False
        try:
            self.screen.refresh(layout=True)
        except Exception as exc:  # no screen yet (pre-mount / teardown)
            log.tui.warning(
                "[tui] compose_area._hide_autocomplete: screen refresh skipped",
                exc_info=exc,
                extra={"_fields": {}},
            )

    def _handle_autocomplete_key(self, key: str) -> bool:
        """Nav-hook router — drive the dropdown while it is open.

        Returns ``True`` iff the key was consumed by the dropdown (so the editor
        must NOT process it).  When the dropdown is closed, or for keys the
        dropdown does not own, returns ``False`` so the editor handles the key
        normally (and ``on_text_area_changed`` refreshes the candidate list).
        """
        # Forward ghost-text accept (Right) — works whether or not the dropdown
        # is open. Falls through (returns False) when there is no ghost or the
        # cursor isn't at the end, so Right moves the cursor normally.
        if key == "right" and self._ghost_suffix:
            return self._accept_ghost()
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
            # Semantic panel rests with NO selection: Enter then SUBMITS the
            # prose (return False), it never hijacks the message. Tab arms the
            # first candidate. A deliberate Down+Enter selects.
            if dropdown.current() is None:
                if key == "enter":
                    return False
                dropdown.move_down()
            self._complete_current()
            return True
        if key == "escape":
            self._hide_autocomplete()
            return True
        return False

    def _complete_current(self) -> None:
        """Accept the highlighted candidate: rewrite editor text + post message.

        Three cases by the kind/level of the open dropdown:

        * COMMAND — replace the whole line with ``/<name> `` and re-open the
          dropdown so a verb command immediately reveals its sub-commands
          (Tab/Enter descend a level).
        * SUB — replace only the trailing partial token with ``<name>``, keeping
          the already-typed command/sub prefix, adding a trailing space (and
          re-opening) when the node has children or args, else closing.
        * OWL — unchanged: replace the last ``@<prefix>`` token.
        """
        dropdown = self._dropdown()
        if dropdown is None:
            return
        # AI-augmented rows (☆ suggested / semantic) carry the FULL invocation
        # and only ever POPULATE the box as editable text — never auto-execute,
        # never re-routed through the command/owl insertion logic below.
        star_row = dropdown.current_row()
        if star_row is not None and star_row.kind in (ROW_SUGGESTED, ROW_SEMANTIC):
            star_editor = self._editor()
            if star_editor is not None:
                self._apply_completion(
                    star_editor, f"{star_row.name} ", star_row.name, "command"
                )
            self._clear_ghost()
            self._hide_autocomplete()
            return
        name = dropdown.current()
        if name is None:
            return
        is_command_kind = self._autocomplete_state.kind == AutocompleteKind.COMMAND
        try:
            editor = self.query_one(f"#{_INPUT_ID}", SubmitTextArea)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area._complete_current: editor not mounted",
                exc_info=exc,
                extra={"_fields": {}},
            )
            return

        if not is_command_kind:
            # Owl mention — replace the last "@<prefix>" token with "@<name> ".
            text = editor.text
            at_idx = text.rfind("@")
            new_text = f"@{name} " if at_idx < 0 else f"{text[:at_idx]}@{name} "
            self._apply_completion(editor, new_text, name, "owl")
            self._hide_autocomplete()
            return

        if self._completion_level == CompletionLevel.SUB:
            # Replace only the trailing partial sub token; keep the prefix.
            new_text, reopen = self._compose_sub_completion(editor.text, name)
        else:
            # Top-level command → "/name " (descend into subs next).
            new_text = f"/{name} "
            reopen = True

        self._apply_completion(editor, new_text, name, "command")
        log.tui.debug(
            "[tui] compose_area._complete_current: decision",
            extra={"_fields": {"name": name, "reopen": reopen}},
        )
        if reopen:
            # Re-derive the dropdown from the new buffer so a verb command/sub
            # with children descends a level; programmatic edits don't fire
            # on_text_area_changed, so drive the recompute explicitly.
            self._show_command_autocomplete(new_text)
        else:
            self._hide_autocomplete()

    def _apply_completion(
        self, editor: SubmitTextArea, new_text: str, name: str, kind: str
    ) -> None:
        """Write the completed text, move the cursor, and post the selection."""
        editor.text = new_text
        editor.move_cursor(editor.document.end)
        self._autogrow(editor)
        self.post_message(
            AutocompleteSelectedMessage(selected=name, completion_type=kind)
        )

    def _compose_sub_completion(self, text: str, name: str) -> tuple[str, bool]:
        """Build the buffer after accepting sub-command ``name``.

        Replaces the trailing partial token (if any) with ``name``, then asks the
        parser whether the resulting node still has something to complete
        (children/args) — that decides the trailing space + re-open.
        """
        ends_with_space = text.endswith(" ")
        head = text if ends_with_space else text.rsplit(" ", 1)[0] + " "
        candidate_buffer = f"{head}{name}"
        # Does the node we just completed take children or args? If so, add a
        # trailing space so the user can keep going (and re-open the dropdown).
        level, items = command_dropdown_items(f"{candidate_buffer} ", self._command_infos)
        has_more = level == CompletionLevel.SUB and bool(items)
        node_takes_args = self._sub_node_takes_args(candidate_buffer)
        if has_more or node_takes_args:
            return (f"{candidate_buffer} ", has_more)
        return (candidate_buffer, False)

    def _sub_node_takes_args(self, buffer: str) -> bool:
        """True when the fully-typed sub path resolves to a node with args."""
        ctx = parse_completion(buffer, self._command_infos)
        if ctx.command is None:
            return False
        info = next(
            (i for i in self._command_infos if i.name == ctx.command), None
        )
        if info is None:
            return False
        body = buffer[1:].split()
        path = body[1:]  # drop the command token
        if not path:
            return False
        node = resolve_path(info.meta.subcommands, path)
        return bool(node and node.args)

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
            self._autogrow(editor)
        except Exception as exc:
            log.tui.warning(
                "[tui] compose_area.action_clear_input: editor not mounted",
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
