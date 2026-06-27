"""Unit tests for AcceptanceChecker — artifact kind (existing) + the NEW general
side-effect re-probe kind (F-12).

The deterministic checker historically observed only ``kind=="artifact"`` (a fresh
saved file); every other declared effect fell through to ``accepted=None`` /
"unknown outcome kind". F-12 adds a second, GENERAL observable post-condition — an
HTTP re-probe (:class:`HttpProbeOutcome`) — that any effect publishing a verifiable
endpoint can map to, while unknown kinds still return a clear, non-crashing verdict.
"""

from __future__ import annotations

import time
from pathlib import Path

from stackowl.objectives.model import ExpectedOutcome
from stackowl.pipeline.acceptance import (
    AcceptanceChecker,
    HttpProbeOutcome,
)


def test_artifact_fresh_file_accepted(tmp_path: Path) -> None:
    started = time.time()
    (tmp_path / "out.bin").write_bytes(b"data")
    checker = AcceptanceChecker()
    verdict = checker.check(
        ExpectedOutcome(kind="artifact", artifact_dir=str(tmp_path)),
        turn_started_at=started,
        acted=True,
    )
    assert verdict.accepted is True


def test_artifact_no_file_refuted(tmp_path: Path) -> None:
    started = time.time()
    missing = tmp_path / "nope"
    checker = AcceptanceChecker()
    verdict = checker.check(
        ExpectedOutcome(kind="artifact", artifact_dir=str(missing)),
        turn_started_at=started,
        acted=True,
    )
    assert verdict.accepted is False


def test_no_declared_outcome_is_noop() -> None:
    checker = AcceptanceChecker()
    assert checker.check(None, turn_started_at=time.time(), acted=True).accepted is None
    assert (
        checker.check(ExpectedOutcome(), turn_started_at=time.time(), acted=True).accepted
        is None
    )


def test_unacted_turn_never_penalized(tmp_path: Path) -> None:
    checker = AcceptanceChecker()
    verdict = checker.check(
        ExpectedOutcome(kind="artifact", artifact_dir=str(tmp_path / "x")),
        turn_started_at=time.time(),
        acted=False,
    )
    assert verdict.accepted is None


# ── F-12: the new general HTTP re-probe kind ─────────────────────────────────


def test_http_probe_success_accepted() -> None:
    checker = AcceptanceChecker(http_prober=lambda _url: True)
    verdict = checker.check(
        HttpProbeOutcome(url="https://example.test/health"),
        turn_started_at=time.time(),
        acted=True,
    )
    assert verdict.accepted is True


def test_http_probe_failure_refuted() -> None:
    checker = AcceptanceChecker(http_prober=lambda _url: False)
    verdict = checker.check(
        HttpProbeOutcome(url="https://example.test/health"),
        turn_started_at=time.time(),
        acted=True,
    )
    assert verdict.accepted is False


def test_http_probe_unobservable_is_no_opinion() -> None:
    checker = AcceptanceChecker(http_prober=lambda _url: None)
    verdict = checker.check(
        HttpProbeOutcome(url="https://example.test/health"),
        turn_started_at=time.time(),
        acted=True,
    )
    assert verdict.accepted is None


def test_http_probe_unacted_not_engaged() -> None:
    calls: list[str] = []

    def _prober(url: str) -> bool:
        calls.append(url)
        return True

    checker = AcceptanceChecker(http_prober=_prober)
    verdict = checker.check(
        HttpProbeOutcome(url="https://example.test/health"),
        turn_started_at=time.time(),
        acted=False,
    )
    assert verdict.accepted is None
    assert calls == []  # never probed a turn that took no action


def test_unknown_kind_non_crashing() -> None:
    """An outcome whose kind the checker does not recognize must yield a clear
    no-opinion verdict, never raise."""

    class _Weird:
        kind = "telepathy"

    checker = AcceptanceChecker()
    verdict = checker.check(
        _Weird(),  # type: ignore[arg-type]
        turn_started_at=time.time(),
        acted=True,
    )
    assert verdict.accepted is None
    assert "unknown" in verdict.reason.lower()
