"""An oversized tool result must SAY it was cut, and by how much.

D03.4 level 3 (a registry cap) plus the signal level 1 never had.

MEASURED 2026-08-29 from `output_len`, already logged on every tool exit. n=1,786
results: median 5,379 chars, p90 24,443, p99 238,058, MAX 4,201,658. 22% exceed
10k; 1.1% exceed 200k. The context window is 262,144 tokens (~1M chars), so the
4.2M-char result is FOUR TIMES the whole window.

THE MAP SAYS "truncation destroys information". Measured, it destroys it SILENTLY
AND UNCOUNTABLY: ToolResult carried no truncation flag, and result truncation
never appeared in the log — the 2,444 "truncated" lines are the skill CATALOG and
tool SCHEMAS, which are prompt-side, not results. Neither the agent nor the
operator could tell it had happened.

That ordering matters: a cap WITHOUT a signal makes the silent loss worse, because
it cuts more and still says nothing. So the flag lands with the cap, not after it.

ENFORCED AT THE ONE CHOKEPOINT. base.Tool.__call__ documents itself as exactly
that — "THIS is the tool chokepoint: every invocation goes through __call__ ...
so a hook here cannot be wired on some paths only". Twelve tools truncate
individually today; the cap is not a thirteenth copy.
"""

from __future__ import annotations

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult


class _BigTool(Tool):
    def __init__(self, size: int, cap: int | None) -> None:
        self._size, self._cap = size, cap

    @property
    def name(self) -> str:
        return "big_tool"

    @property
    def description(self) -> str:
        return "returns a lot"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="read", max_result_size_chars=self._cap,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="x" * self._size)


@pytest.mark.asyncio
async def test_an_oversized_result_is_capped_and_SAYS_SO() -> None:
    """THE regression. Silent loss is the defect, not the cutting."""
    result = await _BigTool(size=50_000, cap=1_000)()

    assert len(result.output) <= 1_000 + 512, "the cap was not applied"
    assert result.truncated is True, "the result was cut and did not say so"
    assert result.original_output_len == 50_000, (
        "the agent cannot tell HOW MUCH was lost — 'some was cut' is not "
        "actionable, '49,000 of 50,000 chars were cut' is"
    )


@pytest.mark.asyncio
async def test_a_normal_result_is_untouched() -> None:
    """The control. 98% of real results are under 50k and must pay nothing."""
    result = await _BigTool(size=500, cap=1_000)()

    assert result.output == "x" * 500
    assert result.truncated is False
    assert result.original_output_len is None


@pytest.mark.asyncio
async def test_a_tool_with_no_cap_is_unchanged() -> None:
    """Level 3 is opt-in per tool. A tool that declares no cap behaves exactly as
    before, so this cannot regress the 12 tools that already self-truncate."""
    result = await _BigTool(size=4_000_000, cap=None)()

    assert len(result.output) == 4_000_000
    assert result.truncated is False


@pytest.mark.asyncio
async def test_the_cut_is_visible_IN_the_output_too() -> None:
    """The flag is for code; the model reads the output.

    A model that cannot see the cut will summarise a truncated page as if it were
    the whole page — which is the "system knows something and says something else"
    shape this tree keeps producing.
    """
    result = await _BigTool(size=50_000, cap=1_000)()

    assert "truncated" in result.output.lower() or "cut" in result.output.lower()
    assert "50000" in result.output or "50,000" in result.output
