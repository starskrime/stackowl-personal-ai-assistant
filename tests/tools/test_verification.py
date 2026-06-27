"""Tests for the verification primitive (Branch 1).

The platform's foundational honesty bit: a tool that CLAIMS an effect must have
that effect OBSERVED before it is trusted. These tests pin:

* ``is_trustworthy_success`` — the one derived predicate every decider reads.
* ``verify_artifact`` — the hardened existence oracle (existence + non-empty +
  FRESHNESS + magic-byte), the helper that catches the stale-file and
  error-page false-positives the party review flagged.
* ``Tool.verify()`` + ``Tool.__call__`` stamping — verification runs at the one
  universal seam, only after a ``success=True`` execute, never raises, and is
  byte-identical for any tool that does not override ``verify()``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.verification import is_trustworthy_success, verify_artifact

# asyncio_mode=auto (pyproject) runs async tests automatically — no marker needed.


# --------------------------------------------------------------- the predicate
def test_trustworthy_success_unverified_falls_back_to_success() -> None:
    # verified=None ⇒ today's behavior exactly (byte-identical).
    assert is_trustworthy_success(True, None) is True
    assert is_trustworthy_success(False, None) is False


def test_trustworthy_success_verified_true_is_trusted() -> None:
    assert is_trustworthy_success(True, True) is True


def test_trustworthy_success_claimed_but_unverified_is_not_trusted() -> None:
    # The whole point: success=True but reality disagreed ⇒ NOT a trustworthy win.
    assert is_trustworthy_success(True, False) is False


# ------------------------------------------------------------- verify_artifact
def test_verify_artifact_none_path_is_no_opinion() -> None:
    assert verify_artifact(None) is None
    assert verify_artifact("") is None


def test_verify_artifact_missing_file_is_false() -> None:
    assert verify_artifact("/nonexistent/path/to/artifact.bin") is False


def test_verify_artifact_empty_file_is_false(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("")
    assert verify_artifact(str(f)) is False


def test_verify_artifact_fresh_nonempty_file_is_true(tmp_path: Path) -> None:
    f = tmp_path / "real.txt"
    f.write_text("content")
    assert verify_artifact(str(f)) is True


def test_verify_artifact_stale_file_fails_freshness(tmp_path: Path) -> None:
    """A predictable-path artifact left by a PREVIOUS run must NOT pass: a backend
    that no-ops while last run's file is still on disk is the disguised --simulate
    bug. Freshness (mtime >= call start) is what catches it."""
    f = tmp_path / "stale.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    # Stamp the file an hour into the PAST, then verify as if the call just started.
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert verify_artifact(str(f), not_before=time.time()) is False


def test_verify_artifact_fresh_passes_freshness(tmp_path: Path) -> None:
    start = time.time()
    f = tmp_path / "fresh.txt"
    f.write_text("written during the call")
    assert verify_artifact(str(f), not_before=start) is True


def test_verify_artifact_magic_byte_mismatch_is_false(tmp_path: Path) -> None:
    """A 9-byte error page saved with an image extension is non-empty but garbage.
    When a kind is declared, the header check rejects it."""
    f = tmp_path / "notreally.png"
    f.write_text("not a png")
    assert verify_artifact(str(f), expect_kind="image") is False


def test_verify_artifact_magic_byte_match_is_true(tmp_path: Path) -> None:
    f = tmp_path / "real.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert verify_artifact(str(f), expect_kind="image") is True


# ----------------------------------------------------- Tool.__call__ stamping
class _ClaimsButProducesNothing(Tool):
    """Reports success=True and names an artifact_path that does not exist."""

    @property
    def name(self) -> str:
        return "fake_claimer"

    @property
    def description(self) -> str:
        return "claims an effect it never produced"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(
            success=True, output="done!", duration_ms=1.0,
            artifact_path="/nonexistent/file/that/was/never/made.bin",
        )

    async def verify(self, args: dict, result: ToolResult, *, started_at: float) -> bool | None:
        return verify_artifact(result.artifact_path, not_before=started_at)


class _RealProducer(Tool):
    """Writes a real file and names it."""

    def __init__(self, target: Path) -> None:
        self._target = target

    @property
    def name(self) -> str:
        return "real_producer"

    @property
    def description(self) -> str:
        return "produces a real artifact"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        self._target.write_text("real output")
        return ToolResult(
            success=True, output=f"wrote {self._target}", duration_ms=1.0,
            artifact_path=str(self._target),
        )

    async def verify(self, args: dict, result: ToolResult, *, started_at: float) -> bool | None:
        return verify_artifact(result.artifact_path, not_before=started_at)


class _NoVerifyOverride(Tool):
    """A normal tool that does NOT override verify() — must stay byte-identical."""

    @property
    def name(self) -> str:
        return "plain"

    @property
    def description(self) -> str:
        return "no verification"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


async def test_call_stamps_verified_false_when_claim_unbacked() -> None:
    result = await _ClaimsButProducesNothing()()
    assert result.success is True            # the self-report is preserved (no laundering)
    assert result.verified is False          # reality check disagreed
    assert is_trustworthy_success(result.success, result.verified) is False


async def test_call_stamps_verified_true_for_real_artifact(tmp_path: Path) -> None:
    result = await _RealProducer(tmp_path / "out.txt")()
    assert result.success is True
    assert result.verified is True
    assert is_trustworthy_success(result.success, result.verified) is True


async def test_call_no_verify_override_is_byte_identical() -> None:
    result = await _NoVerifyOverride()()
    assert result.success is True
    assert result.verified is None           # never stamped → unchanged behavior


async def test_call_does_not_verify_a_failed_execute() -> None:
    """verify() runs ONLY after success=True — a failed execute is never second-guessed."""

    class _Fails(Tool):
        @property
        def name(self) -> str:
            return "fails"

        @property
        def description(self) -> str:
            return "x"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=False, output="", error="boom", duration_ms=1.0)

        async def verify(self, args: dict, result: ToolResult, *, started_at: float) -> bool | None:
            raise AssertionError("verify must not run on a failed execute")

    result = await _Fails()()
    assert result.success is False
    assert result.verified is None


async def test_call_verify_exception_falls_back_to_none() -> None:
    """A verify() that raises must never block a real success — falls back to None."""

    class _VerifyBoom(Tool):
        @property
        def name(self) -> str:
            return "verify_boom"

        @property
        def description(self) -> str:
            return "x"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True, output="ok", duration_ms=1.0)

        async def verify(self, args: dict, result: ToolResult, *, started_at: float) -> bool | None:
            raise RuntimeError("verification backend exploded")

    result = await _VerifyBoom()()
    assert result.success is True
    assert result.verified is None           # fail-safe: unverified, not failed


# ----------------------------- F-25: a self-asserted verified=True is a CLAIM
class _SelfStampsVerifiedTrue(Tool):
    """Reports success=True AND stamps verified=True in execute() — laundering its
    own self-report into a 'reality-confirmed' verdict the seam never made."""

    def __init__(self, seam_verdict: bool | None = None) -> None:
        self._seam_verdict = seam_verdict
        self.verify_calls = 0

    @property
    def name(self) -> str:
        return "self_stamper"

    @property
    def description(self) -> str:
        return "self-asserts verification"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="done!", duration_ms=1.0, verified=True)

    async def verify(self, args: dict, result: ToolResult, *, started_at: float) -> bool | None:
        self.verify_calls += 1
        return self._seam_verdict


async def test_call_self_asserted_verified_true_is_demoted_without_seam_confirmation() -> None:
    """The exact B1 failure: a tool self-stamps verified=True with no independent
    seam confirmation (verify() returns None) — it must NOT be trusted as proof."""
    tool = _SelfStampsVerifiedTrue(seam_verdict=None)
    result = await tool()
    assert result.success is True            # the self-report is preserved
    assert tool.verify_calls == 1            # the seam STILL ran (claim, not proof)
    assert result.verified is None           # unconfirmed True demoted — never trusted as proof


async def test_call_self_asserted_verified_true_overridden_by_seam_false() -> None:
    """When the seam can check and DISAGREES, its False verdict takes precedence."""
    tool = _SelfStampsVerifiedTrue(seam_verdict=False)
    result = await tool()
    assert result.verified is False          # seam verdict wins over the self-claim
    assert is_trustworthy_success(result.success, result.verified) is False


async def test_call_self_asserted_verified_true_confirmed_by_seam_stays_true() -> None:
    """A self-claim the seam independently CONFIRMS is a legitimate verified win."""
    tool = _SelfStampsVerifiedTrue(seam_verdict=True)
    result = await tool()
    assert result.verified is True


async def test_call_self_reported_verified_false_is_preserved() -> None:
    """A tool that honestly admits verified=False is left alone (not second-guessed)."""

    class _AdmitsFalse(Tool):
        @property
        def name(self) -> str:
            return "admits_false"

        @property
        def description(self) -> str:
            return "x"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True, output="ok", duration_ms=1.0, verified=False)

        async def verify(self, args: dict, result: ToolResult, *, started_at: float) -> bool | None:
            raise AssertionError("seam must not run on a self-reported verified=False")

    result = await _AdmitsFalse()()
    assert result.verified is False


# --------------------------- F-24: bounded retry-once on classifiably-transient
class _FlakyTool(Tool):
    """Raises ``exc`` on the first N calls, then returns success. ``severity``
    controls the manifest's action_severity (read tools are retry-eligible)."""

    def __init__(self, exc: Exception, fail_times: int, severity: str = "read") -> None:
        self._exc = exc
        self._fail_times = fail_times
        self._severity = severity
        self.calls = 0

    @property
    def name(self) -> str:
        return "flaky"

    @property
    def description(self) -> str:
        return "fails transiently"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity=self._severity,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        return ToolResult(success=True, output="recovered", duration_ms=1.0)


async def test_call_retries_once_on_transient_read_tool() -> None:
    tool = _FlakyTool(ConnectionError("Connection reset by peer"), fail_times=1, severity="read")
    result = await tool()
    assert tool.calls == 2                   # retried exactly once
    assert result.success is True


async def test_call_does_not_retry_consequential_tool_on_transient() -> None:
    tool = _FlakyTool(ConnectionError("Connection reset"), fail_times=1, severity="consequential")
    result = await tool()
    assert tool.calls == 1                   # NEVER retried — double-execution risk
    assert result.success is False


async def test_call_does_not_retry_write_tool_on_transient() -> None:
    tool = _FlakyTool(ConnectionError("Connection refused"), fail_times=1, severity="write")
    result = await tool()
    assert tool.calls == 1                   # write is effectful — not retried here
    assert result.success is False


async def test_call_does_not_retry_non_transient_error() -> None:
    tool = _FlakyTool(ValueError("bad argument"), fail_times=1, severity="read")
    result = await tool()
    assert tool.calls == 1                   # not classifiably-transient → no retry
    assert result.success is False
    assert result.error is not None and "bad argument" in result.error


async def test_call_transient_retry_second_failure_is_wrapped() -> None:
    tool = _FlakyTool(ConnectionError("Connection reset"), fail_times=2, severity="read")
    result = await tool()
    assert tool.calls == 2                   # one retry max, then give up
    assert result.success is False
    assert result.error is not None and "Connection reset" in result.error
