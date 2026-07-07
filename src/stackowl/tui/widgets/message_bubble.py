"""Chat bubble widgets — a single mounted bubble plus its alignment row.

The conversation transcript is a scrollable column of :class:`MessageRow`
instances, each holding exactly one :class:`MessageBubble`.  User bubbles are
right-aligned, agent bubbles left-aligned; an agent bubble's body updates in
place as streaming chunks arrive so a whole turn renders into ONE bubble.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Button, Static

from stackowl.commands.response import CANCEL_SENTINEL, Action, make_confirm_response
from stackowl.infra.observability import log
from stackowl.tui.i18n import localize
from stackowl.tui.messages.compose import ComposeSubmittedMessage

if TYPE_CHECKING:
    from rich.console import RenderableType
    from textual.app import ComposeResult


class MessageBubble(Static):
    """A single chat bubble: a role label plus a body that streams in place.

    Implemented as a :class:`~textual.widgets.Static` whose ``render`` returns a
    Rich :class:`~rich.console.Group` of (dim label, body).  A ``Static`` with
    ``width: auto`` + a border reliably hugs its content — a hand-rolled
    ``Widget``/container wrapping child ``Static``\\ s collapses to zero size
    (nested auto-width does not resolve).  The body is rendered as plain
    :class:`~rich.text.Text` (never markup-parsed) so arbitrary content —
    including ``[`` from user input or ``[1]`` citation markers — renders
    verbatim and cannot inject Rich markup.
    """

    DEFAULT_CSS = """
    MessageBubble {
        width: auto;
        max-width: 80%;
        height: auto;
        border: round $color-border;
        padding: 0 1;
        margin: 0 1;
    }
    MessageBubble.-user {
        border: round $color-success;
    }
    MessageBubble.-agent {
        border: round $color-accent-dim;
    }
    """

    def __init__(
        self,
        *,
        role: str,
        owl_name: str | None = None,
        text: str = "",
    ) -> None:
        role_class = "-user" if role == "user" else "-agent"
        super().__init__(classes=role_class)
        log.tui.debug(
            "[tui] message_bubble.__init__: entry",
            extra={
                "_fields": {
                    "role": role,
                    "owl_name": owl_name,
                    "text_len": len(text),
                }
            },
        )
        self._role: str = role
        # User label is a localized string; the agent label is the owl name,
        # which is data (not English) and must NOT be localized.
        if role == "user":
            self._label: str = localize("transcript.role.you")
        else:
            self._label = owl_name or ""
        self._buffer: str = text

    def render(self) -> RenderableType:
        """Render the bubble as a dim label stacked over the (plain) body text."""
        label = Text(self._label, style="dim")
        body = Text(self._buffer)
        return Group(label, body)

    def append(self, text: str) -> None:
        """Append text to the body and re-render the bubble in place."""
        log.tui.debug(
            "[tui] message_bubble.append: entry",
            extra={"_fields": {"role": self._role, "add_len": len(text)}},
        )
        self._buffer += text
        # layout=True so the auto-sized bubble grows to fit the new content.
        self.refresh(layout=True)
        log.tui.debug(
            "[tui] message_bubble.append: exit",
            extra={"_fields": {"role": self._role, "buffer_len": len(self._buffer)}},
        )


class MessageRow(Horizontal):
    """A full-width row that aligns its single bubble left (agent) / right (user)."""

    DEFAULT_CSS = """
    MessageRow {
        width: 100%;
        height: auto;
    }
    MessageRow.-user {
        align-horizontal: right;
    }
    MessageRow.-agent {
        align-horizontal: left;
    }
    """

    def __init__(self, bubble: MessageBubble, *, role: str) -> None:
        role_class = "-user" if role == "user" else "-agent"
        super().__init__(classes=role_class)
        log.tui.debug(
            "[tui] message_row.__init__: entry",
            extra={"_fields": {"role": role}},
        )
        self._bubble: MessageBubble = bubble

    def compose(self) -> ComposeResult:
        yield self._bubble


class _ActionButton(Button):
    """A ``Button`` carrying the :class:`Action` it replays on press."""

    def __init__(self, action: Action) -> None:
        super().__init__(action.label)
        self.action_data = action


class ActionButtonRow(Horizontal):
    """Tappable follow-up buttons rendered below a command reply's bubble.

    One button per :class:`Action`. A non-destructive tap replays the
    action's ``command`` through the SAME path a typed slash command already
    uses — posting :class:`ComposeSubmittedMessage`, which bubbles up to
    :meth:`StackOwlApp.on_compose_submitted_message` (republish on the
    EventBus + echo into the transcript). A destructive tap swaps this row's
    buttons in place for :func:`make_confirm_response`'s Yes/Cancel pair
    (mirrors Telegram's two-tap rule — see ``channels/telegram/command_buttons.py``).

    ``_resolved`` closes the same class of gap the Telegram review caught
    (a stale button still tappable after Cancel): it is checked-and-set
    SYNCHRONOUSLY at the top of the handler, before any ``await``, so a
    press that lands while a previous press's ``remove_children``/``mount_all``
    is still in flight is a no-op rather than a double-dispatch. Replacing
    the row's buttons resets the flag so the new (e.g. confirm) buttons are
    themselves tappable exactly once each.
    """

    DEFAULT_CSS = """
    ActionButtonRow {
        width: auto;
        height: auto;
        margin: 0 1;
    }
    ActionButtonRow Button {
        margin: 0 1 0 0;
        min-width: 3;
    }
    """

    def __init__(self, actions: tuple[Action, ...]) -> None:
        super().__init__()
        self._actions = actions
        self._resolved = False

    def compose(self) -> ComposeResult:
        for action in self._actions:
            yield _ActionButton(action)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self._resolved:
            # Stale re-delivery (race with an in-flight remove/mount, or a
            # button that should already be gone) — ignore, never re-dispatch.
            log.tui.debug(
                "[tui] action_button_row.on_button_pressed: ignored — already resolved",
                extra={"_fields": {}},
            )
            return
        button = event.button
        if not isinstance(button, _ActionButton):
            return
        self._resolved = True
        action = button.action_data
        log.tui.debug(
            "[tui] action_button_row.on_button_pressed: entry",
            extra={"_fields": {"command": action.command, "destructive": action.destructive}},
        )
        if action.destructive:
            confirm = make_confirm_response(action)
            await self._replace_actions(confirm.actions)
            return
        if action.command == CANCEL_SENTINEL:
            await self._replace_actions(())
            return
        self.post_message(ComposeSubmittedMessage(text=action.command))
        log.tui.debug(
            "[tui] action_button_row.on_button_pressed: exit — replayed",
            extra={"_fields": {"command": action.command}},
        )

    async def _replace_actions(self, actions: tuple[Action, ...]) -> None:
        """Swap this row's buttons for ``actions`` in place (no new row)."""
        await self.remove_children()
        self._actions = actions
        if actions:
            await self.mount_all(_ActionButton(a) for a in actions)
            # New buttons are freshly tappable; only re-arm once they exist —
            # an empty replacement (Cancel) has nothing left to tap, so
            # staying resolved is correct (and harmless either way).
            self._resolved = False
