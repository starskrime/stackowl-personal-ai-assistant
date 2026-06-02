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
from textual.widgets import Static

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

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
