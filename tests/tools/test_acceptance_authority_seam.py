"""ADR-1 — the Tool.__call__ AcceptanceAuthority seam.

A tool that DECLARES a PostCondition has its `verified` set by the authority's
observation of reality (distinct from the actor). Flag OFF ⇒ the seam is skipped ⇒
byte-identical. A tool that declares nothing is byte-identical regardless of flag.
These prove the keystone invariant at the real seam every effect passes through.
"""

from __future__ import annotations

import pytest

import stackowl.tools.base as base_mod
from stackowl.pipeline.acceptance_authority import DeliveryAck, NonEmptyText
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.verification import is_trustworthy_success


class _TextTool(Tool):
    """Declares a NonEmptyText post-condition; returns whatever it is told to."""

    def __init__(self, output: str, *, claims_verified: bool | None = None) -> None:
        self._output = output
        self._claims_verified = claims_verified

    name = "fake_text"
    description = "test"

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(
            success=True,
            output=self._output,
            duration_ms=1.0,
            verified=self._claims_verified,
        )

    def post_condition(self, args, result):  # type: ignore[no-untyped-def]
        return NonEmptyText()


class _PlainTool(Tool):
    """Declares NO post-condition — must be byte-identical regardless of flag."""

    name = "fake_plain"
    description = "test"

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="", duration_ms=1.0)


class _DeliveryTool(Tool):
    def __init__(self, acked: bool | None) -> None:
        self._acked = acked

    name = "fake_send"
    description = "test"

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="sent", duration_ms=1.0)

    def post_condition(self, args, result):  # type: ignore[no-untyped-def]
        return DeliveryAck(acked=self._acked, channel="telegram")


@pytest.fixture
def authority_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_mod, "_acceptance_authority_enabled", lambda: True)


@pytest.fixture
def authority_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_mod, "_acceptance_authority_enabled", lambda: False)


# --- Flag ON: success tracks reality -------------------------------------------


@pytest.mark.asyncio
async def test_empty_output_refuted_when_authority_on(authority_on: None) -> None:
    result = await _TextTool(output="")()
    assert result.success is True  # self-report preserved (claim vs confirmation)
    assert result.verified is False  # reality refuted it
    assert is_trustworthy_success(result.success, result.verified) is False


@pytest.mark.asyncio
async def test_real_output_verified_when_authority_on(authority_on: None) -> None:
    result = await _TextTool(output="a real answer")()
    assert result.verified is True
    assert is_trustworthy_success(result.success, result.verified) is True


@pytest.mark.asyncio
async def test_self_stamp_cannot_override_authority(authority_on: None) -> None:
    # Tool claims verified=True but produced nothing → authority refutes the stamp.
    result = await _TextTool(output="", claims_verified=True)()
    assert result.verified is False


@pytest.mark.asyncio
async def test_delivery_ack_failure_refuted(authority_on: None) -> None:
    assert (await _DeliveryTool(acked=False)()).verified is False
    assert (await _DeliveryTool(acked=True)()).verified is True
    # Lossy boundary (no ack) ⇒ no opinion, never a fabricated failure.
    assert (await _DeliveryTool(acked=None)()).verified is None


# --- Flag OFF and no-declaration: byte-identical --------------------------------


@pytest.mark.asyncio
async def test_flag_off_is_byte_identical(authority_off: None) -> None:
    # Same empty-output tool, flag OFF → the seam never runs → verified stays None.
    result = await _TextTool(output="")()
    assert result.verified is None


@pytest.mark.asyncio
async def test_undeclared_tool_byte_identical_when_on(authority_on: None) -> None:
    # A tool with no post_condition() is unaffected even when the flag is ON.
    result = await _PlainTool()()
    assert result.verified is None
