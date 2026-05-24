"""MemoryReviewPanel — review staged facts (approve / reject / skip).

The panel iterates through a list of :class:`StagedFact` one at a time,
flagging anything whose ``source_type`` matches one of the configured
``sensitive_categories`` so the user notices before approving it.  Action
buttons debounce for 200ms to defeat double-keystroke approvals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from textual.binding import Binding
from textual.widgets import RichLog

from stackowl.infra.observability import log
from stackowl.memory.models import StagedFact
from stackowl.tui.i18n import localize
from stackowl.tui.widgets.overlay_panel import OverlayPanel

if TYPE_CHECKING:
    from textual.app import ComposeResult


_LOG_ID = "memory_review_log"
_DEBOUNCE_SECONDS = 0.2


class _BridgeLike(Protocol):
    """Subset of :class:`MemoryBridge` the review panel needs (async)."""

    async def force_promote(self, fact_id: str) -> bool: ...
    async def delete(self, fact_id: str) -> None: ...


class MemoryReviewPanel(OverlayPanel):
    """Review staged facts: Approve / Reject / Skip."""

    overlay_name = "memory_review"

    DEFAULT_CSS = """
    MemoryReviewPanel {
        layer: overlay;
        width: 70%;
        height: 60%;
        background: $color-bg-elevated;
        border: solid $color-accent;
        offset: 15% 20%;
    }
    """

    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("r", "reject", "Reject"),
        Binding("n", "skip", "Next"),
        Binding("p", "previous", "Previous"),
        Binding("escape", "close_overlay", "Close"),
    ]

    def __init__(
        self,
        *,
        bridge: _BridgeLike | None = None,
        sensitive_categories: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] memory_review_panel.__init__: entry",
            extra={
                "_fields": {
                    "has_bridge": bridge is not None,
                    "sensitive_count": len(sensitive_categories),
                }
            },
        )
        self._bridge: _BridgeLike | None = bridge
        self._sensitive: frozenset[str] = frozenset(sensitive_categories)
        self._facts: list[StagedFact] = []
        self._index: int = 0
        self._buttons_disabled: bool = False

    # ----------------------------------------------------------------- accessors
    @property
    def facts(self) -> tuple[StagedFact, ...]:
        return tuple(self._facts)

    @property
    def index(self) -> int:
        return self._index

    @property
    def buttons_disabled(self) -> bool:
        return self._buttons_disabled

    @property
    def sensitive_categories(self) -> frozenset[str]:
        return self._sensitive

    # ----------------------------------------------------------------- compose
    def compose(self) -> ComposeResult:
        yield RichLog(highlight=False, markup=False, wrap=True, id=_LOG_ID)

    # ----------------------------------------------------------------- API
    def load_facts(self, facts: list[StagedFact]) -> None:
        """Seed the panel with the staged facts to review."""
        log.tui.debug(
            "[tui] memory_review_panel.load_facts: entry",
            extra={"_fields": {"count": len(facts)}},
        )
        self._facts = list(facts)
        self._index = 0
        self._render_current()

    def is_sensitive(self, fact: StagedFact) -> bool:
        """Return ``True`` when ``fact.source_type`` is in sensitive categories."""
        return fact.source_type in self._sensitive

    # ----------------------------------------------------------------- render
    def _render_current(self) -> None:
        """Render the current fact, or close the panel if exhausted."""
        log.tui.debug(
            "[tui] memory_review_panel._render_current: entry",
            extra={
                "_fields": {"index": self._index, "total": len(self._facts)}
            },
        )
        if not self._facts or self._index >= len(self._facts):
            self.close()
            return
        fact = self._facts[self._index]
        lines: list[str] = []
        if self.is_sensitive(fact):
            lines.append(f"[!] {localize('memory.review.sensitive_warning')}")
        lines.append(
            f"{localize('memory.review.position')}: "
            f"{self._index + 1}/{len(self._facts)}"
        )
        lines.append(
            f"{localize('memory.review.confidence')}: {fact.confidence:.2f}"
        )
        lines.append(f"{localize('memory.review.source')}: {fact.source_type}")
        lines.append("")
        lines.append(fact.content)
        lines.append("")
        lines.append(
            f"[a] {localize('memory.review.approve')}  "
            f"[r] {localize('memory.review.reject')}  "
            f"[n] {localize('memory.review.skip')}"
        )
        self._write_lines(tuple(lines))

    def _write_lines(self, lines: tuple[str, ...]) -> None:
        """Push ``lines`` to the embedded RichLog (no-op when unmounted)."""
        try:
            log_widget = self.query_one(f"#{_LOG_ID}", RichLog)
        except Exception as exc:
            log.tui.warning(
                "[tui] memory_review_panel._write_lines: RichLog unavailable",
                exc_info=exc,
                extra={"_fields": {"line_count": len(lines)}},
            )
            return
        log_widget.clear()
        for line in lines:
            log_widget.write(line)

    # ----------------------------------------------------------------- actions
    def action_approve(self) -> None:
        """Promote the current fact and advance."""
        if self._buttons_disabled or not self._facts:
            return
        self._debounce_buttons()
        fact = self._facts[self._index]
        if self._bridge is not None:
            self._run_bridge_call("approve", fact, self._bridge.force_promote)
        self._next_fact()

    def action_reject(self) -> None:
        """Delete the current fact and advance."""
        if self._buttons_disabled or not self._facts:
            return
        self._debounce_buttons()
        fact = self._facts[self._index]
        if self._bridge is not None:
            self._run_bridge_call("reject", fact, self._bridge.delete)
        self._next_fact()

    def action_skip(self) -> None:
        """Skip without modifying the fact."""
        if not self._facts:
            return
        self._next_fact()

    def action_previous(self) -> None:
        """Step backwards (clamped to zero)."""
        if not self._facts:
            return
        self._index = max(0, self._index - 1)
        self._render_current()

    # ----------------------------------------------------------------- internals
    def _run_bridge_call(self, op: str, fact: StagedFact, fn) -> None:  # type: ignore[no-untyped-def]
        """Schedule an async bridge call onto the running loop, swallowing failures with logs."""
        try:
            self.run_worker(fn(fact.fact_id), exclusive=False)
        except Exception as exc:
            log.tui.warning(
                "[tui] memory_review_panel._run_bridge_call: schedule failed",
                exc_info=exc,
                extra={"_fields": {"op": op, "fact_id": fact.fact_id}},
            )

    def _next_fact(self) -> None:
        """Advance to the next fact; close the overlay when exhausted."""
        self._index += 1
        if self._index >= len(self._facts):
            self.close()
        else:
            self._render_current()

    def _debounce_buttons(self) -> None:
        """Disable action buttons for 200ms to prevent double-trigger."""
        self._buttons_disabled = True
        try:
            self.set_timer(_DEBOUNCE_SECONDS, self._enable_buttons)
        except Exception as exc:
            log.tui.warning(
                "[tui] memory_review_panel._debounce_buttons: set_timer failed",
                exc_info=exc,
                extra={"_fields": {}},
            )
            self._buttons_disabled = False

    def _enable_buttons(self) -> None:
        """Timer callback — re-enable the action buttons."""
        self._buttons_disabled = False
