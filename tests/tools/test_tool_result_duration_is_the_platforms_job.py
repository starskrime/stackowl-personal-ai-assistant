"""D16.1 implement, slice 1 — a tool author should not have to time their own call.

FOUND BY BEING THE FIRST USER of the plugin surface (2026-08-16). Driving a real
throwaway plugin through the real loader surfaced this:

    ToolResult.duration_ms is REQUIRED with no default, sitting between two
    optional fields, so the obvious ``ToolResult(success=True, output=...)``
    raises a pydantic ValidationError.

The author is made to supply a timing number they have not measured — while the
platform times the call itself in two places (``tools/base.py`` computes it on the
failure path, ``execute.py:1808/1827`` computes it at dispatch). Asking the author
for it is a SECOND WRITER to a fact the platform already owns, which is how the
two drift.

WHY A BARE DEFAULT WOULD BE WORSE THAN THE FRICTION. Defaulting to 0.0 and
stopping there would let every author-omitted call report a duration of zero into
latency metrics and the cost tracker — silently wrong data, which is worse than a
loud ValidationError. So the default comes WITH the platform stamping its own
measurement over it.

THE RULE: the author may omit it, and if they do the platform's measurement wins.
An author who DOES supply one keeps it — a tool that measures something more
meaningful than wall-clock (an upstream API's own reported latency, say) is not
overridden.
"""

from __future__ import annotations

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult

pytestmark = pytest.mark.asyncio


class _OmitsDuration(Tool):
    """What a plugin author naturally writes on their first attempt."""

    @property
    def name(self) -> str:
        return "omits_duration"

    @property
    def description(self) -> str:
        return "A tool that does not time itself, because timing is not its job."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(action_severity="read")

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="done")


class _SuppliesDuration(_OmitsDuration):
    @property
    def name(self) -> str:
        return "supplies_duration"

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="done", duration_ms=1234.5)


class TestTheAuthorMayOmitIt:
    async def test_constructing_without_duration_does_not_raise(self) -> None:
        """The exact line a first-time plugin author writes."""
        r = ToolResult(success=True, output="hello")

        assert r.success is True
        assert r.duration_ms == 0.0

    async def test_the_platform_stamps_its_own_measurement(self) -> None:
        """A default of 0.0 alone would feed zeros into latency metrics. The point
        is not that the field is optional — it is that the platform, which already
        times the call, fills it in."""
        result = await _OmitsDuration()()

        assert result.success is True
        assert result.duration_ms > 0.0, (
            "the platform did not stamp its own timing — an omitted duration would "
            "report as zero latency"
        )


class TestTheAuthorMayStillOwnIt:
    async def test_a_supplied_duration_is_not_clobbered(self) -> None:
        """A tool that measures something more meaningful than wall-clock keeps its
        own number. The platform fills a GAP; it does not overrule a measurement."""
        result = await _SuppliesDuration()()

        assert result.duration_ms == 1234.5


class TestNothingElseMoves:
    async def test_the_other_fields_survive_the_stamp(self) -> None:
        """ToolResult is frozen, so the stamp goes through model_copy. That is the
        moment a field gets dropped if the copy is written carelessly."""
        result = await _OmitsDuration()()

        assert result.output == "done"
        assert result.error is None
        assert result.side_effect_committed is True

    async def test_a_failure_still_carries_a_real_duration(self) -> None:
        """The failure path already computed its own timing before this change and
        must keep doing so."""

        class _Boom(_OmitsDuration):
            @property
            def name(self) -> str:
                return "boom"

            async def execute(self, **kwargs: object) -> ToolResult:
                raise RuntimeError("nope")

        result = await _Boom()()

        assert result.success is False
        assert result.duration_ms > 0.0
