"""EvolutionBadge — 3-beat pulse badge surfaced on an OwlCard after evolution."""

from __future__ import annotations

import os

from textual.binding import Binding
from textual.widget import Widget

from stackowl.infra.observability import log
from stackowl.tui.messages import OpenEvolutionInspectionMessage

_PULSE_TOTAL_SECONDS = 0.9  # 3 × 300ms beats


class EvolutionBadge(Widget):
    """Pulse badge revealing that an owl's DNA just mutated."""

    DEFAULT_CSS = """
    EvolutionBadge {
        background: $color-accent;
        color: $color-text-primary;
        width: auto;
        height: 1;
    }
    EvolutionBadge.-beat-dim {
        background: $color-accent-dim;
    }
    """

    BINDINGS = [
        Binding("enter", "open_inspection", "Inspect"),
    ]

    def __init__(
        self,
        owl_name: str,
        changed_traits: dict[str, tuple[object, object]],
    ) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] evolution_badge.__init__: entry",
            extra={
                "_fields": {
                    "owl_name": owl_name,
                    "trait_count": len(changed_traits),
                }
            },
        )
        self._owl_name: str = owl_name
        self._changed_traits: dict[str, tuple[object, object]] = dict(changed_traits)
        self._reduced_motion: bool = (
            os.environ.get("STACKOWL_REDUCED_MOTION") == "1"
        )

    @property
    def owl_name(self) -> str:
        return self._owl_name

    @property
    def changed_traits(self) -> dict[str, tuple[object, object]]:
        return dict(self._changed_traits)

    @property
    def pulse_seconds(self) -> float:
        return _PULSE_TOTAL_SECONDS

    def on_mount(self) -> None:
        """Schedule the auto-removal timer after the 3-beat pulse."""
        log.tui.debug(
            "[tui] evolution_badge.on_mount: entry",
            extra={"_fields": {"owl_name": self._owl_name}},
        )
        try:
            self.set_timer(_PULSE_TOTAL_SECONDS, self._safe_remove)
        except Exception as exc:
            log.tui.warning(
                "[tui] evolution_badge.on_mount: set_timer failed",
                exc_info=exc,
                extra={"_fields": {"owl_name": self._owl_name}},
            )

    def render(self) -> str:
        return self._owl_name

    def action_open_inspection(self) -> None:
        """Binding action — request the inspection overlay."""
        try:
            self.post_message(
                OpenEvolutionInspectionMessage(
                    owl_name=self._owl_name,
                    changed_traits=dict(self._changed_traits),
                )
            )
        except Exception as exc:
            log.tui.warning(
                "[tui] evolution_badge.action_open_inspection: post_message failed",
                exc_info=exc,
                extra={"_fields": {"owl_name": self._owl_name}},
            )

    def _safe_remove(self) -> None:
        try:
            self.remove()
        except Exception as exc:
            log.tui.warning(
                "[tui] evolution_badge._safe_remove: remove failed",
                exc_info=exc,
                extra={"_fields": {"owl_name": self._owl_name}},
            )
