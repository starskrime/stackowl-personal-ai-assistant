"""depth_cap/width_cap — profile-resolved delegation caps (Phase 0, coding-capability build plan).

Interactive stays byte-identical to the pre-Phase-0 constants; autonomous
(unattended, e.g. ObjectiveDriverHandler) gets the wider budget; any unknown
profile fails safe to the stricter interactive cap.
"""

from __future__ import annotations

from stackowl.owls.delegation_limits import (
    MAX_CONCURRENT_DELEGATIONS,
    MAX_CONCURRENT_DELEGATIONS_AUTONOMOUS,
    MAX_DELEGATION_DEPTH,
    MAX_DELEGATION_DEPTH_AUTONOMOUS,
    depth_cap,
    width_cap,
)


def test_interactive_profile_matches_original_constants() -> None:
    assert depth_cap("interactive") == MAX_DELEGATION_DEPTH
    assert width_cap("interactive") == MAX_CONCURRENT_DELEGATIONS


def test_autonomous_profile_is_wider() -> None:
    assert depth_cap("autonomous") == MAX_DELEGATION_DEPTH_AUTONOMOUS
    assert width_cap("autonomous") == MAX_CONCURRENT_DELEGATIONS_AUTONOMOUS
    assert MAX_DELEGATION_DEPTH_AUTONOMOUS > MAX_DELEGATION_DEPTH
    assert MAX_CONCURRENT_DELEGATIONS_AUTONOMOUS > MAX_CONCURRENT_DELEGATIONS


def test_unknown_profile_fails_safe_to_interactive() -> None:
    assert depth_cap("bogus") == MAX_DELEGATION_DEPTH
    assert width_cap("") == MAX_CONCURRENT_DELEGATIONS


def test_autonomous_caps_still_bounded_by_shared_host_ceiling() -> None:
    """The moat note in delegation_limits.py: depth/width widen the LOGICAL tree
    shape only — they never touch MAX_INFLIGHT_PIPELINES, the physical host cap."""
    from stackowl.owls.delegation_limits import MAX_INFLIGHT_PIPELINES

    assert MAX_INFLIGHT_PIPELINES == 4  # unchanged by this phase
