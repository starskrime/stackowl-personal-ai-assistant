"""EvolutionInspectionPanel — overlay listing trait deltas after an evolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import RichLog

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize
from stackowl.tui.widgets.overlay_panel import OverlayPanel

if TYPE_CHECKING:
    from textual.app import ComposeResult


_LOG_ID = "evolution_inspection_log"


def _delta(old: object, new: object) -> float | None:
    """Return a signed numeric delta when both values are numeric, else ``None``."""
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return float(new) - float(old)
    return None


class EvolutionInspectionPanel(OverlayPanel):
    """Lists DNA trait deltas: name, old → new, signed delta."""

    overlay_name = "evolution_inspection"

    DEFAULT_CSS = """
    EvolutionInspectionPanel {
        layer: overlay;
        width: 60%;
        height: 50%;
        background: $color-bg-elevated;
        border: solid $color-accent;
        offset: 20% 25%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] evolution_inspection_panel.__init__: entry",
            extra={"_fields": {}},
        )
        self._owl_name: str = ""
        self._changed_traits: dict[str, tuple[object, object]] = {}

    @property
    def owl_name(self) -> str:
        return self._owl_name

    @property
    def changed_traits(self) -> dict[str, tuple[object, object]]:
        return dict(self._changed_traits)

    def compose(self) -> ComposeResult:
        yield RichLog(highlight=False, markup=True, wrap=True, id=_LOG_ID)

    def load(
        self,
        owl_name: str,
        changed_traits: dict[str, tuple[object, object]],
    ) -> None:
        """Render the trait-delta table for ``owl_name``."""
        log.tui.debug(
            "[tui] evolution_inspection_panel.load: entry",
            extra={
                "_fields": {
                    "owl_name": owl_name,
                    "trait_count": len(changed_traits),
                }
            },
        )
        self._owl_name = owl_name
        self._changed_traits = dict(changed_traits)
        self._write_lines(self._build_lines())

    def _build_lines(self) -> tuple[str, ...]:
        """Compose the user-facing trait-delta lines."""
        header = (
            f"{localize('evolution.inspection.header')}: {self._owl_name}"
        )
        lines: list[str] = [header, ""]
        if not self._changed_traits:
            lines.append(localize("evolution.inspection.no_changes"))
            return tuple(lines)
        for name, pair in self._changed_traits.items():
            old, new = pair
            delta = _delta(old, new)
            line = f"{name}: {old} → {new}"
            if delta is not None:
                sign = "+" if delta >= 0 else ""
                color = "$color-success" if delta >= 0 else "$color-warning"
                line += f"  [{color}]({sign}{delta:g})[/]"
            lines.append(line)
        return tuple(lines)

    def _write_lines(self, lines: tuple[str, ...]) -> None:
        """Push ``lines`` into the embedded RichLog (no-op when unmounted)."""
        try:
            log_widget = self.query_one(f"#{_LOG_ID}", RichLog)
        except Exception as exc:
            log.tui.warning(
                "[tui] evolution_inspection_panel._write_lines: RichLog unavailable",
                exc_info=exc,
                extra={"_fields": {"line_count": len(lines)}},
            )
            return
        log_widget.clear()
        for line in lines:
            log_widget.write(line)
