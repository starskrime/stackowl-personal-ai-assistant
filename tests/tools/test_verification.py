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

from stackowl.tools.base import Tool, ToolResult
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
