"""StackOwlApp — the 5-zone Textual application class.

Mounts the five headline TUI widgets:

* :class:`Banner` — pinned StackOwl wordmark docked at the top of the screen
* :class:`ParliamentPanel` — overlay shown during parliament sessions
* :class:`ConversationView` — primary streaming response surface
* :class:`PipelineStrip` — live pipeline-step status indicator
* :class:`ComposeArea` — bottom input area with autocomplete

Output flows in via EventBus → :class:`UIStateCoordinator` → Textual
``post_message``; the widgets handle their own messages. Input flows out
via :class:`ComposeSubmittedMessage` (Textual message bubble) → captured
here and published to EventBus under ``compose_submitted`` so the
:class:`CLIAdapter` can build an :class:`IngressMessage` from it.

Per the wiring plan (gleaming-finding-puppy.md, Commit D), this class
replaces the raw-RichLog-and-Input ``_StackOwlApp`` previously embedded
in ``channels/cli_adapter.py``. The legacy class stays in that file for
back-compat in tests; this is the production app.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult

from stackowl.infra.observability import log
from stackowl.tui.i18n_strings import install_default_translations
from stackowl.tui.messages import (
    ComposeAreaStateMessage,
    ComposeSubmittedMessage,
    CostUpdatedMessage,
    DegradedProviderMessage,
    ParliamentClosedMessage,
    ParliamentRoundMessage,
    ParliamentRoundStartedMessage,
    ParliamentStartedMessage,
    PipelineStepMessage,
    ProviderChangedMessage,
    ResponseChunkMessage,
    SynthesisArrivedMessage,
    UserTurnMessage,
)
from stackowl.tui.widgets.banner import Banner
from stackowl.tui.widgets.compose_area import ComposeArea
from stackowl.tui.widgets.compose_helpers import CommandInfo
from stackowl.tui.widgets.conversation_view import ConversationView
from stackowl.tui.widgets.parliament_panel import ParliamentPanel
from stackowl.tui.widgets.pipeline_strip import PipelineStrip

# StackOwl design tokens, mirrored from tui/styles/stackowl.tcss. These get
# merged into the Textual variable table via App.get_css_variables() so they
# are in scope when every widget's DEFAULT_CSS is parsed. CSS_PATH does NOT
# work for this — variables defined in a loaded stylesheet are not visible
# to DEFAULT_CSS at the time it's parsed.
_DESIGN_TOKENS: dict[str, str] = {
    "color-bg": "#0d1117",
    "color-bg-elevated": "#161b22",
    "color-surface": "#21262d",
    "color-border": "#30363d",
    "color-text-primary": "#e6edf3",
    "color-text-secondary": "#8b949e",
    "color-text-muted": "#6e7681",
    "color-accent": "#58a6ff",
    "color-accent-dim": "#1f6feb",
    "color-success": "#3fb950",
    "color-warning": "#d29922",
    "color-error": "#f85149",
    "color-parliament": "#bc8cff",
    "color-banner-amber": "#d29922",
    "color-banner-red": "#f85149",
    "color-banner-rule": "#3fb950",
}

if TYPE_CHECKING:
    from textual.message import Message
    from textual.widget import Widget

    from stackowl.events.bus import EventBus


_COMPOSE_EVENT = "compose_submitted"

# Explicit delivery routing: message type → (target widget class, handler name).
# Messages are FrozenMessage dataclasses and CANNOT travel through Textual's
# message pump (the pump mutates `_no_default_action`, which raises
# FrozenInstanceError) and the pump does not propagate down to child widgets
# anyway. Delivery is therefore a DIRECT handler call on the UI thread, routed
# through this table by StackOwlApp.deliver().
_DELIVERY_ROUTES: dict[type[Message], tuple[type[Widget], str]] = {
    ResponseChunkMessage: (ConversationView, "on_response_chunk_message"),
    UserTurnMessage: (ConversationView, "on_user_turn_message"),
    PipelineStepMessage: (PipelineStrip, "on_pipeline_step_message"),
    DegradedProviderMessage: (PipelineStrip, "on_degraded_provider_message"),
    ProviderChangedMessage: (PipelineStrip, "on_provider_changed_message"),
    CostUpdatedMessage: (PipelineStrip, "on_cost_updated_message"),
    ParliamentStartedMessage: (ParliamentPanel, "on_parliament_started_message"),
    ParliamentRoundStartedMessage: (
        ParliamentPanel,
        "on_parliament_round_started_message",
    ),
    ParliamentRoundMessage: (ParliamentPanel, "on_parliament_round_message"),
    SynthesisArrivedMessage: (ParliamentPanel, "on_synthesis_arrived_message"),
    ParliamentClosedMessage: (ParliamentPanel, "on_parliament_closed_message"),
    ComposeAreaStateMessage: (ComposeArea, "on_compose_area_state_message"),
}


class StackOwlApp(App[None]):
    """5-zone Textual app: banner + parliament overlay + conversation + pipeline + compose."""

    # The top-level layout is fully docked (banner top, compose bottom,
    # conversation 1fr) and never needs to scroll, so the Screen must not show
    # or reserve a vertical scrollbar — otherwise it eats the rightmost column
    # and insets every bordered widget by one. App.CSS is always applied.
    CSS = """
    Screen {
        overflow: hidden hidden;
    }
    """

    def get_css_variables(self) -> dict[str, str]:
        """Merge our design tokens into Textual's variable table.

        Textual calls this before parsing widget DEFAULT_CSS, so the
        ``$color-bg-elevated``, ``$color-parliament``, etc. references in
        every widget's stylesheet resolve cleanly. Variables defined via
        ``CSS_PATH`` are NOT visible to DEFAULT_CSS — this is the only
        Textual-supported injection point.
        """
        base = super().get_css_variables()
        return {**base, **_DESIGN_TOKENS}

    def __init__(
        self,
        event_bus: EventBus,
        *,
        command_names: Iterable[str] | None = None,
        command_infos: Iterable[CommandInfo] | None = None,
        owl_names: Iterable[str] | None = None,
    ) -> None:
        install_default_translations()
        super().__init__()
        self._event_bus = event_bus
        self._command_names: list[str] = list(command_names or [])
        self._command_infos: list[CommandInfo] = list(command_infos or [])
        self._owl_names: list[str] = list(owl_names or [])
        log.tui.debug(
            "[tui] StackOwlApp.__init__",
            extra={"_fields": {
                "command_count": len(self._command_names),
                "command_info_count": len(self._command_infos),
                "owl_count": len(self._owl_names),
            }},
        )

    def compose(self) -> ComposeResult:
        """Yield the 5 widgets in display order (top → bottom)."""
        yield Banner()
        yield ParliamentPanel()
        yield ConversationView()
        yield PipelineStrip()
        yield ComposeArea(
            command_names=self._command_names,
            command_infos=self._command_infos,
            owl_names=self._owl_names,
        )

    def deliver(self, message: Message) -> None:
        """Route a coordinator-built message to its target widget.

        Delivery is a DIRECT handler invocation on the UI thread — NOT
        ``post_message``. Textual's message pump does not propagate messages
        down to child widgets (their ``on_*_message`` handlers would never
        fire), and these messages are FrozenMessage dataclasses that raise
        ``FrozenInstanceError`` if pumped through any handler-bearing loop.
        The route table :data:`_DELIVERY_ROUTES` maps each message type to its
        sink widget + handler. Self-healing: a missing widget (e.g. during
        teardown) is logged and skipped, never crashes the UI.
        """
        msg_type = type(message)
        log.tui.debug(
            "[tui] StackOwlApp.deliver: entry",
            extra={"_fields": {"message_type": msg_type.__name__}},
        )
        route = _DELIVERY_ROUTES.get(msg_type)
        if route is None:
            # Genuinely orphaned message type — no UI sink wired yet. Make the
            # gap loud and honest rather than silently dropping the message.
            log.tui.warning(
                "[tui] StackOwlApp.deliver: no UI sink wired for message type "
                "— see docs/tui-output-sinks-phase2-backlog.md",
                extra={"_fields": {"message_type": msg_type.__name__}},
            )
            return
        widget_cls, handler_name = route
        log.tui.debug(
            "[tui] StackOwlApp.deliver: route found",
            extra={
                "_fields": {
                    "message_type": msg_type.__name__,
                    "widget": widget_cls.__name__,
                    "handler": handler_name,
                }
            },
        )
        try:
            widget = self.query_one(widget_cls)
        except Exception as exc:
            log.tui.warning(
                "[tui] StackOwlApp.deliver: target widget unavailable — dropping",
                exc_info=exc,
                extra={
                    "_fields": {
                        "message_type": msg_type.__name__,
                        "widget": widget_cls.__name__,
                    }
                },
            )
            return
        getattr(widget, handler_name)(message)
        log.tui.debug(
            "[tui] StackOwlApp.deliver: exit",
            extra={
                "_fields": {
                    "message_type": msg_type.__name__,
                    "widget": widget_cls.__name__,
                }
            },
        )

    def on_compose_submitted_message(self, message: ComposeSubmittedMessage) -> None:
        """ComposeArea bubble — republish on the EventBus for CLIAdapter to pick up.

        Going through the EventBus (rather than a direct queue) keeps the
        widget → CLIAdapter boundary clean and uniform with how every other
        UI event already flows.
        """
        log.tui.debug(
            "[tui] StackOwlApp.on_compose_submitted_message: republishing",
            extra={"_fields": {"text_len": len(message.text)}},
        )
        # Republish for CLIAdapter first — this must always run, even when the
        # transcript widget isn't mounted (unit tests drive this directly).
        self._event_bus.emit(_COMPOSE_EVENT, {"text": message.text})
        # Echo the user's own turn into the transcript through the single
        # delivery path. deliver() self-heals on a missing view, so input is
        # never blocked.
        self.deliver(UserTurnMessage(text=message.text))
