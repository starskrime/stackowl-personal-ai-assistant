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

import pytest

from stackowl.objectives.model import ExpectedOutcome
from stackowl.pipeline import acceptance as acceptance_mod
from stackowl.pipeline.acceptance import (
    AcceptanceChecker,
    AcceptanceVerdict,
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


# ── F-13: "unobservable" (could-not-observe) vs "observed-absent" ─────────────
#
# A FS/transport error must NOT silently read as a pass for a DECLARED consequential
# outcome. accepted stays None (no fabricated failure), but a distinct ``unobservable``
# flag lets the caller treat it as soft-fail/retry instead of an implicit pass.


def test_http_probe_unobservable_flags_unobservable() -> None:
    checker = AcceptanceChecker(http_prober=lambda _url: None)
    verdict = checker.check(
        HttpProbeOutcome(url="https://example.test/health"),
        turn_started_at=time.time(),
        acted=True,
    )
    assert verdict.accepted is None
    assert verdict.unobservable is True


def test_artifact_unobservable_flags_unobservable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the filesystem observation to error out (could-not-observe).
    monkeypatch.setattr(acceptance_mod, "_dir_has_fresh_file", lambda *_a, **_k: None)
    checker = AcceptanceChecker()
    verdict = checker.check(
        ExpectedOutcome(kind="artifact", artifact_dir=str(tmp_path)),
        turn_started_at=time.time(),
        acted=True,
    )
    assert verdict.accepted is None
    assert verdict.unobservable is True


def test_observed_absent_is_not_unobservable(tmp_path: Path) -> None:
    """A directory we COULD observe that simply holds no fresh file is a refutation
    (accepted False), never an unobservable no-opinion."""
    verdict = AcceptanceChecker().check(
        ExpectedOutcome(kind="artifact", artifact_dir=str(tmp_path / "nope")),
        turn_started_at=time.time(),
        acted=True,
    )
    assert verdict.accepted is False
    assert verdict.unobservable is False


def test_skipped_verdicts_are_not_unobservable() -> None:
    """A skip (no declared outcome / unacted) is not the same as could-not-observe."""
    checker = AcceptanceChecker()
    no_outcome = checker.check(None, turn_started_at=time.time(), acted=True)
    assert no_outcome.accepted is None and no_outcome.unobservable is False
    unacted = checker.check(
        ExpectedOutcome(kind="artifact", artifact_dir="x"),
        turn_started_at=time.time(),
        acted=False,
    )
    assert unacted.accepted is None and unacted.unobservable is False


# ── F-1: ``engaged`` — distinguishes a verdict that was RUN from one SKIPPED ──


def test_engaged_property_classifies_run_vs_skipped() -> None:
    # Skipped: no opinion, not because we tried and failed to observe.
    assert AcceptanceVerdict(None, "no declared outcome").engaged is False
    # Run + passed / refuted: engaged.
    assert AcceptanceVerdict(True, "observed").engaged is True
    assert AcceptanceVerdict(False, "refuted").engaged is True
    # Run but could-not-observe: still engaged (we attempted observation).
    assert AcceptanceVerdict(None, "unobservable", unobservable=True).engaged is True
