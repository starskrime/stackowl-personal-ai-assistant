"""ADR-1 — AcceptanceAuthority: success ⟺ measured, never asserted.

The keystone invariant: no action reports a trustworthy success for a DECLARED
effect without a Verdict(accepted=True) from an observation distinct from the
actor. A tool that self-stamps verified=True with a failing post-condition is
ignored. These tests fail on pre-ADR code (no authority / no PostCondition over
non-file effect kinds) and pass after.
"""

from __future__ import annotations

import time

import pytest

from stackowl.pipeline.acceptance_authority import (
    AcceptanceAuthority,
    ArtifactFresh,
    Custom,
    DeliveryAck,
    HttpOk,
    NonEmptyText,
    NoPostCondition,
    Verdict,
    final_verified,
)
from stackowl.tools.verification import is_trustworthy_success


@pytest.fixture
def authority() -> AcceptanceAuthority:
    return AcceptanceAuthority()


# --- No post-condition declared ⇒ no opinion ⇒ byte-identical fallback ---------


def test_no_postcondition_is_no_opinion(authority: AcceptanceAuthority) -> None:
    v = authority.observe(None, success=True, verified=None, output="hi")
    assert v.accepted is None
    v2 = authority.observe(NoPostCondition(), success=True, verified=None, output="hi")
    assert v2.accepted is None


def test_final_verified_defers_to_self_report_when_no_opinion() -> None:
    # verdict with no opinion must NOT change the existing verified signal.
    v = Verdict(None, "no post-condition declared")
    assert final_verified(success=True, verified=None, verdict=v) is None
    assert final_verified(success=True, verified=True, verdict=v) is True
    assert final_verified(success=False, verified=False, verdict=v) is False


# --- NonEmptyText: the empty-as-success class (F-20/23 provider, F-82/83 MCP) --


def test_non_empty_text_refutes_empty_output(authority: AcceptanceAuthority) -> None:
    v = authority.observe(NonEmptyText(), success=True, output="")
    assert v.accepted is False
    v_ws = authority.observe(NonEmptyText(), success=True, output="   \n\t ")
    assert v_ws.accepted is False


def test_non_empty_text_accepts_real_output(authority: AcceptanceAuthority) -> None:
    v = authority.observe(NonEmptyText(), success=True, output="a real answer")
    assert v.accepted is True


# --- The keystone invariant: success tracks reality, self-stamp is ignored -----


def test_self_stamp_cannot_launder_a_failing_postcondition(
    authority: AcceptanceAuthority,
) -> None:
    # Tool claims success=True AND verified=True, but the post-condition (non-empty
    # output) is refuted by reality → the trustworthy signal is False.
    v = authority.observe(NonEmptyText(), success=True, verified=True, output="")
    assert v.accepted is False
    final = final_verified(success=True, verified=True, verdict=v)
    assert final is False
    assert is_trustworthy_success(True, final) is False


def test_measured_success_is_trustworthy(authority: AcceptanceAuthority) -> None:
    v = authority.observe(NonEmptyText(), success=True, verified=None, output="ok")
    final = final_verified(success=True, verified=None, verdict=v)
    assert is_trustworthy_success(True, final) is True


# --- DeliveryAck: the failed-send-reports-success class (F-29/30) ---------------


def test_delivery_ack_tri_state(authority: AcceptanceAuthority) -> None:
    assert authority.observe(DeliveryAck(acked=True), success=True).accepted is True
    assert authority.observe(DeliveryAck(acked=False), success=True).accepted is False
    # Unknowable ack (lossy boundary) ⇒ no opinion, never a fabricated failure.
    assert authority.observe(DeliveryAck(acked=None), success=True).accepted is None


# --- ArtifactFresh: reuses verify_artifact (file kind) -------------------------


def test_artifact_fresh_refutes_absent_file(
    authority: AcceptanceAuthority, tmp_path
) -> None:
    missing = tmp_path / "nope.png"
    v = authority.observe(
        ArtifactFresh(path=str(missing)), success=True, started_at=time.time()
    )
    assert v.accepted is False


def test_artifact_fresh_accepts_real_file(
    authority: AcceptanceAuthority, tmp_path
) -> None:
    started = time.time()
    f = tmp_path / "out.txt"
    f.write_text("data")
    v = authority.observe(ArtifactFresh(path=str(f)), success=True, started_at=started)
    assert v.accepted is True


# --- HttpOk: injectable prober (F-32) ------------------------------------------


def test_http_ok_uses_prober() -> None:
    auth_live = AcceptanceAuthority(http_prober=lambda _u: True)
    auth_dead = AcceptanceAuthority(http_prober=lambda _u: False)
    auth_unreach = AcceptanceAuthority(http_prober=lambda _u: None)
    assert auth_live.observe(HttpOk(url="http://x"), success=True).accepted is True
    assert auth_dead.observe(HttpOk(url="http://x"), success=True).accepted is False
    assert auth_unreach.observe(HttpOk(url="http://x"), success=True).accepted is None


# --- Custom: escape hatch / judge-of-last-resort -------------------------------


def test_custom_probe(authority: AcceptanceAuthority) -> None:
    assert authority.observe(Custom(probe=lambda: True), success=True).accepted is True
    assert authority.observe(Custom(probe=lambda: False), success=True).accepted is False
    # A raising probe never propagates — no opinion.
    def boom() -> bool:
        raise RuntimeError("probe blew up")

    assert authority.observe(Custom(probe=boom), success=True).accepted is None


def test_observe_never_raises(authority: AcceptanceAuthority) -> None:
    # A malformed post-condition must yield no-opinion, never raise into a turn.
    v = authority.observe(ArtifactFresh(path=None), success=True, started_at=None)
    assert v.accepted is None
