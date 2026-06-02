"""ParliamentPanel — overlay rendering a Parliament session in progress.

Lifecycle: ``ParliamentStartedMessage`` (roll-call) → ``ParliamentRoundStartedMessage``
(divider, prior round panels collapse) → ``ParliamentRoundMessage`` (owl text
streamed into per-owl sub-panels) → ``SynthesisArrivedMessage`` (consensus,
disagreements, recommendation, confidence) → ``ParliamentClosedMessage``
(panel hidden).  Heavy string assembly lives in :mod:`parliament_panel_helpers`;
the owl sub-panel widget lives in :mod:`owl_round_panel`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import RichLog

from stackowl.infra.observability import log
from stackowl.tui.glyphs import GLYPH_PARLIAMENT, GLYPH_SEPARATOR
from stackowl.tui.i18n import localize
from stackowl.tui.messages import (
    ParliamentClosedMessage,
    ParliamentRoundMessage,
    ParliamentRoundStartedMessage,
    ParliamentStartedMessage,
    SynthesisArrivedMessage,
)
from stackowl.tui.widgets.owl_round_panel import OwlRoundPanel
from stackowl.tui.widgets.parliament_panel_helpers import (
    OnboardingStore,
    build_synthesis_sections,
    format_rollcall,
    format_round_header,
    synthesis_lines,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult

_LOG_ID = "parliament_log"
_ONBOARDING_KEY = "parliament_panel_tip"


class ParliamentPanel(Widget):
    """Overlay narrating a Parliament session: roll-call → rounds → synthesis."""

    DEFAULT_CSS = """
    ParliamentPanel {
        layer: overlay;
        display: none;
        width: 80%;
        height: 80%;
        background: $color-bg-elevated;
        border: double $color-parliament;
        offset: 10% 10%;
    }
    """

    BINDINGS = [
        Binding("escape", "close_parliament", "Close parliament"),
        Binding("c", "close_parliament", "Close parliament"),
    ]

    def __init__(self, *, onboarding_store: OnboardingStore | None = None) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] parliament_panel.__init__: entry",
            extra={"_fields": {"onboarding": onboarding_store is not None}},
        )
        self._session_id: str = ""
        self._round_panels: dict[str, OwlRoundPanel] = {}
        self._current_round: int = 0
        self._onboarding_tip_shown: bool = False
        self._onboarding_store: OnboardingStore | None = onboarding_store

    # ------------------------------------------------------------------ access
    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def current_round(self) -> int:
        return self._current_round

    @property
    def round_panels(self) -> dict[str, OwlRoundPanel]:
        return dict(self._round_panels)

    @property
    def onboarding_tip_shown(self) -> bool:
        return self._onboarding_tip_shown

    def compose(self) -> ComposeResult:
        yield RichLog(highlight=False, markup=False, wrap=True, id=_LOG_ID)

    # ------------------------------------------------------------------ messages
    def on_parliament_started_message(self, msg: ParliamentStartedMessage) -> None:
        """Roll-call render + reveal."""
        log.tui.debug(
            "[tui] parliament_panel.on_parliament_started_message: entry",
            extra={
                "_fields": {
                    "session_id": msg.session_id,
                    "owl_count": len(msg.owl_names),
                    "trigger": msg.trigger,
                }
            },
        )
        self._session_id = msg.session_id
        self._current_round = 1
        self._round_panels = {name: OwlRoundPanel(name) for name in msg.owl_names}
        line = format_rollcall(msg.owl_names, str(GLYPH_PARLIAMENT))
        header = format_round_header(localize("parliament.round"), 1)
        self._write_lines((line, header))
        self.display = True

    def on_parliament_round_started_message(
        self, msg: ParliamentRoundStartedMessage
    ) -> None:
        """Collapse prior rounds, bump current round, emit divider."""
        log.tui.debug(
            "[tui] parliament_panel.on_parliament_round_started_message: entry",
            extra={
                "_fields": {
                    "session_id": msg.session_id,
                    "round": msg.round_number,
                }
            },
        )
        if msg.round_number > 1:
            for panel in self._round_panels.values():
                panel.collapse()
        self._current_round = msg.round_number
        header = format_round_header(localize("parliament.round"), msg.round_number)
        self._write_lines((header,))

    def on_parliament_round_message(self, msg: ParliamentRoundMessage) -> None:
        """Update each owl sub-panel with its response text."""
        log.tui.debug(
            "[tui] parliament_panel.on_parliament_round_message: entry",
            extra={
                "_fields": {
                    "session_id": msg.session_id,
                    "round": msg.round_number,
                    "owls": len(msg.owl_responses),
                }
            },
        )
        for owl_name, text in msg.owl_responses.items():
            panel = self._round_panels.get(owl_name)
            if panel is None:
                panel = OwlRoundPanel(owl_name)
                self._round_panels[owl_name] = panel
            panel.append_text(text)

    def on_synthesis_arrived_message(self, msg: SynthesisArrivedMessage) -> None:
        """Render synthesis sections: consensus, disagreements, recommendation."""
        log.tui.debug(
            "[tui] parliament_panel.on_synthesis_arrived_message: entry",
            extra={
                "_fields": {
                    "session_id": msg.session_id,
                    "confidence": msg.confidence,
                    "disagreements": len(msg.disagreements),
                }
            },
        )
        sections = build_synthesis_sections(
            consensus=msg.consensus,
            recommendation=msg.recommendation,
            confidence=msg.confidence,
            disagreements=msg.disagreements,
            consensus_label=localize("parliament.consensus"),
            disagreements_label=localize("parliament.disagreements"),
            recommendation_label=localize("parliament.recommendation"),
            separator=str(GLYPH_SEPARATOR),
        )
        self._write_lines(synthesis_lines(sections))

    def on_parliament_closed_message(self, msg: ParliamentClosedMessage) -> None:
        """Hide the panel."""
        log.tui.debug(
            "[tui] parliament_panel.on_parliament_closed_message: entry",
            extra={"_fields": {"session_id": msg.session_id}},
        )
        self.display = False

    # ------------------------------------------------------------------ writing
    def _write_lines(self, lines: tuple[str, ...]) -> None:
        """Push lines to the panel's RichLog (no-op when not yet mounted)."""
        try:
            log_widget = self.query_one(f"#{_LOG_ID}", RichLog)
        except Exception as exc:
            log.tui.warning(
                "[tui] parliament_panel._write_lines: RichLog unavailable",
                exc_info=exc,
                extra={"_fields": {"line_count": len(lines)}},
            )
            return
        for line in lines:
            log_widget.write(line)

    # ------------------------------------------------------------------ onboarding
    def check_onboarding(self, db_path: Path | str | None = None) -> bool:
        """Show one-time Parliament tip if not previously recorded."""
        log.tui.debug(
            "[tui] parliament_panel.check_onboarding: entry",
            extra={"_fields": {"db_path": str(db_path) if db_path else None}},
        )
        store = self._onboarding_store
        if store is None and db_path is not None:
            store = OnboardingStore(Path(db_path))
        if store is None:
            log.tui.warning(
                "[tui] parliament_panel.check_onboarding: no store configured",
                extra={"_fields": {}},
            )
            return False
        if store.was_shown(_ONBOARDING_KEY):
            return False
        store.mark_shown(_ONBOARDING_KEY)
        self._onboarding_tip_shown = True
        self._write_lines((localize("parliament.tip"),))
        return True

    # ------------------------------------------------------------------ actions
    def action_close_parliament(self) -> None:
        """Binding action — dismiss the parliament panel."""
        log.tui.debug(
            "[tui] parliament_panel.action_close_parliament: entry",
            extra={"_fields": {"session_id": self._session_id}},
        )
        try:
            self.display = False
        except Exception as exc:
            log.tui.warning(
                "[tui] parliament_panel.action_close_parliament: hide failed",
                exc_info=exc,
                extra={"_fields": {"session_id": self._session_id}},
            )
