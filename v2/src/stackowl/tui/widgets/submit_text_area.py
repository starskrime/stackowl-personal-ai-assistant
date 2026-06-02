"""SubmitTextArea — multiline editor that submits on Enter, newlines on Shift+Enter.

Wraps Textual's :class:`TextArea` so the compose box can grow vertically and
support native paste / copy / selection across platforms, while keeping the
familiar chat ergonomic: pressing **Enter** sends the message and pressing
**Shift+Enter** inserts a literal newline.  Every other key behaves as a normal
text editor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from collections.abc import Callable


class SubmitTextArea(TextArea):
    """A :class:`TextArea` that submits on Enter and inserts a newline on Shift+Enter.

    On **Enter** it posts a :class:`SubmitTextArea.Submitted` message carrying the
    current editor text and suppresses the default newline insertion.  On
    **Shift+Enter** it inserts a literal ``"\\n"`` and does not submit.  All other
    keys fall through to the standard :class:`TextArea` editing behaviour.
    """

    DEFAULT_CSS = """
    SubmitTextArea {
        height: auto;
        min-height: 1;
        background: $color-bg-elevated;
        color: $color-text-primary;
        border: none;
    }
    """

    class Submitted(Message):
        """Posted when the user presses Enter — carries the editor text.

        Bubbles to the enclosing :class:`ComposeArea`, which strips, validates,
        and republishes it as a ``ComposeSubmittedMessage``.
        """

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    #: Optional navigation hook.  When set and a key is pressed, it is consulted
    #: BEFORE the editor's own handling: if it returns ``True`` the key is
    #: considered consumed (e.g. the autocomplete dropdown moved its highlight)
    #: and never reaches the editor — no submit, no cursor move, no insert.
    nav_hook: Callable[[str], bool] | None = None

    def set_nav_hook(self, hook: Callable[[str], bool] | None) -> None:
        """Install (or clear) the navigation hook consulted on every key."""
        log.tui.debug(
            "[tui] submit_text_area.set_nav_hook: entry",
            extra={"_fields": {"has_hook": hook is not None}},
        )
        self.nav_hook = hook

    async def _on_key(self, event: events.Key) -> None:
        """Intercept Enter / Shift+Enter; defer every other key to TextArea.

        Verified empirically against the pinned Textual version that ``run_test``
        delivers ``event.key == "enter"`` for Enter and ``"shift+enter"`` for
        Shift+Enter, and that both are distinguishable.
        """
        log.tui.debug(
            "[tui] submit_text_area._on_key: entry",
            extra={"_fields": {"key": event.key}},
        )
        # Navigation hook gets first refusal: when the autocomplete dropdown is
        # open it consumes nav keys (up/down/tab/enter/escape) so they drive the
        # dropdown instead of submitting / moving the cursor.
        hook = self.nav_hook
        if hook is not None and hook(event.key):
            log.tui.debug(
                "[tui] submit_text_area._on_key: decision — consumed by nav_hook",
                extra={"_fields": {"key": event.key}},
            )
            event.prevent_default()
            event.stop()
            return
        if event.key == "enter":
            log.tui.debug(
                "[tui] submit_text_area._on_key: submit",
                extra={"_fields": {"text_len": len(self.text)}},
            )
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "shift+enter":
            log.tui.debug(
                "[tui] submit_text_area._on_key: newline",
                extra={"_fields": {"text_len": len(self.text)}},
            )
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        log.tui.debug(
            "[tui] submit_text_area._on_key: exit — passthrough",
            extra={"_fields": {"key": event.key}},
        )
        await super()._on_key(event)
