"""WhyCommand — /why returns the pipeline step trace from the last PipelineState (FR199)."""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.pipeline.state import PipelineState


class WhyCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "why"

    @property
    def description(self) -> str:
        return "Show the pipeline step trace from the last request."

    async def handle(self, args: str, state: PipelineState) -> str:
        lines = [f"Last pipeline step: {state.pipeline_step}"]
        if state.tool_calls:
            lines.append(f"Tool calls: {len(state.tool_calls)}")
            for tc in state.tool_calls:
                lines.append(f"  • {tc.tool_name} — {tc.duration_ms:.0f}ms")
        if state.errors:
            lines.append(f"Errors: {len(state.errors)}")
            for e in state.errors:
                lines.append(f"  ✗ {e}")
        return "\n".join(lines)
