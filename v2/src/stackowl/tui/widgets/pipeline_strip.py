"""PipelineStrip — top-of-screen pipeline status widget."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget

from stackowl.infra.observability import log
from stackowl.tui.glyphs import GLYPH_STEP_COMPLETE, GLYPH_STEP_EMPTY
from stackowl.tui.messages import (
    CostUpdatedMessage,
    DegradedProviderMessage,
    PipelineStepMessage,
    ProviderChangedMessage,
)


class PipelineStrip(Widget):
    """Two-row status strip rendered at the top of the screen.

    Row 1: per-step glyph train + active step name.
    Row 2: agent / provider / cost summary line.
    """

    DEFAULT_CSS = """
    PipelineStrip {
        height: 2;
        layer: top;
        background: $color-surface;
        color: $color-text-secondary;
    }
    """

    step_name: reactive[str] = reactive("")
    step_index: reactive[int] = reactive(0)
    total_steps: reactive[int] = reactive(8)
    active_agents: reactive[int] = reactive(0)
    provider: reactive[str] = reactive("none")
    tier: reactive[str] = reactive("powerful")
    cost_today: reactive[float] = reactive(0.0)
    degraded_reason: reactive[str] = reactive("")

    def on_pipeline_step_message(self, message: PipelineStepMessage) -> None:
        log.tui.debug(
            "[tui] pipeline_strip.on_pipeline_step_message: entry",
            extra={
                "_fields": {
                    "step_name": message.step_name,
                    "step_index": message.step_index,
                    "total_steps": message.total_steps,
                }
            },
        )
        self.step_name = message.step_name
        self.step_index = message.step_index
        self.total_steps = message.total_steps

    def on_provider_changed_message(self, message: ProviderChangedMessage) -> None:
        log.tui.debug(
            "[tui] pipeline_strip.on_provider_changed_message: entry",
            extra={
                "_fields": {"provider": message.provider_name, "tier": message.tier}
            },
        )
        self.provider = message.provider_name
        self.tier = message.tier

    def on_cost_updated_message(self, message: CostUpdatedMessage) -> None:
        log.tui.debug(
            "[tui] pipeline_strip.on_cost_updated_message: entry",
            extra={"_fields": {"cost_today": message.cost_today}},
        )
        self.cost_today = message.cost_today

    def on_degraded_provider_message(self, message: DegradedProviderMessage) -> None:
        log.tui.debug(
            "[tui] pipeline_strip.on_degraded_provider_message: entry",
            extra={
                "_fields": {
                    "provider": message.provider_name,
                    "tier": message.tier,
                    "reason": message.reason,
                }
            },
        )
        self.provider = message.provider_name
        self.tier = message.tier
        self.degraded_reason = message.reason

    def render(self) -> str:
        steps = ""
        for i in range(self.total_steps):
            glyph = GLYPH_STEP_COMPLETE if i < self.step_index else GLYPH_STEP_EMPTY
            steps += str(glyph)
        row1 = f"{steps}  {self.step_name}"
        row2 = (
            f"active_agents={self.active_agents}  "
            f"provider={self.provider}:{self.tier}  "
            f"cost_today=${self.cost_today:.2f}"
        )
        return f"{row1}\n{row2}"
