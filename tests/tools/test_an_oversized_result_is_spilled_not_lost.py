"""D03.4 level 2 — an oversized result is written where it can be re-read.

Level 3 caps it and level 1 says so, but the cut characters are still GONE. The
measured worst case is 4,201,658 characters from browser_extract — four times the
whole context window. Truncation cannot make that usable; only storage can.

WHERE IT LIVES, and why not where the reference puts it. Bakir chose the sandbox
temp dir to match the reference. Measured afterwards, that cannot hold these
results: browser_extract — the entire >100k tail, all 26 of them — has no sandbox
reference at all, and ~/.stackowl/sandbox holds only `seccomp` because
BwrapScratch creates a scratch per run and its own docstring says it "is removed
after the run". The reference runs its tools INSIDE its sandbox; StackOwl runs
them in the core process and sandboxes only code execution, so there is no
sandbox in scope when the overflowing tools run.

So the spill goes under ~/.stackowl/sandbox/tool_results/<trace>/ — the same
EPHEMERAL lifetime the reference gives it, but reachable by the tools that
actually overflow. Assumption stated rather than guessed.

THE PATH MUST REACH THE MODEL. A file nobody is told about is a write with no
reader — the single most common defect shape in this tree. The path goes in the
output text, not only in a field.
"""

from __future__ import annotations

import pathlib

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
        return ToolResult(success=True, output="A" * self._size)


@pytest.mark.asyncio
async def test_the_cut_characters_are_written_to_disk() -> None:
    """THE regression. Capping without spilling still destroys the information."""
    result = await _BigTool(size=50_000, cap=1_000)()

    assert result.spill_path, "the result was cut and nothing was kept"
    body = pathlib.Path(result.spill_path).read_text(encoding="utf-8")
    assert len(body) == 50_000, f"the spill is not the FULL result: {len(body)}"
    assert body == "A" * 50_000


@pytest.mark.asyncio
async def test_the_model_is_TOLD_where_it_went() -> None:
    """A file nobody is told about is a write with no reader.

    The flag and the path are fields; the model reads the output text.
    """
    result = await _BigTool(size=50_000, cap=1_000)()

    assert result.spill_path in result.output, (
        "the spill path is not in the output, so the model cannot re-read it"
    )


@pytest.mark.asyncio
async def test_a_result_that_fits_is_never_spilled() -> None:
    """The control. 98% of real results are under 50k and must touch no disk."""
    result = await _BigTool(size=500, cap=1_000)()

    assert result.spill_path is None
    assert result.truncated is False


@pytest.mark.asyncio
async def test_a_failed_spill_still_returns_the_capped_result() -> None:
    """B5. Losing the spill must not lose the answer too.

    If the disk is full or the path is unwritable, the tool call still succeeds
    with its capped output — degraded, not broken.
    """
    import stackowl.tools.base as mod

    original = mod._spill_dir
    mod._spill_dir = lambda: pathlib.Path("/proc/nonexistent/cannot-write")
    try:
        result = await _BigTool(size=50_000, cap=1_000)()
    finally:
        mod._spill_dir = original

    assert result.success is True
    assert result.truncated is True
    assert len(result.output) <= 1_000 + 512
    assert result.spill_path is None


def test_the_mechanism_is_NOT_dormant() -> None:
    """A cap nobody sets never fires, and the whole item ships as decoration.

    max_result_size_chars defaults to None, so levels 2 and 3 do nothing until a
    tool declares one. This is the "finished feature ships ON, not dormant" rule,
    and it is the same built-but-not-wired shape as the Supervisor the channel
    loops did not use.

    browser_extract produced ALL 26 measured results above 100k chars — if any
    tool carries a cap it must be that one.
    """
    from stackowl.tools.browser.tools import BrowserExtractTool

    cap = BrowserExtractTool().manifest.max_result_size_chars
    assert cap, "browser_extract has no result cap — D03.4 ships dormant"
    assert cap >= 92_000, (
        f"a cap of {cap} sits below the measured p90 of 92,090, so ordinary "
        "pages would be truncated"
    )
    assert cap <= 300_000, (
        f"a cap of {cap} is above the measured p95 of 287,161, so the tail this "
        "item exists for would pass straight through"
    )
