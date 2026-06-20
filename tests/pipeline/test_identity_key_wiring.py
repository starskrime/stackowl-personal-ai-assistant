"""Task 2 — PipelineState.identity_key field + gateway resolution.

(a) Field tests: the field exists, defaults to "", and round-trips.
(b) Gateway resolution: with an IdentityResolver wired into StepServices,
    a mapped session_id resolves to its identity_key; an unmapped one stays as itself.
"""
from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.tenancy.identity import IdentityResolver


# ────────────────────────────────────────────────────────────────── helpers

def _state(**kw: object) -> PipelineState:
    base = dict(
        trace_id="t",
        session_id="s",
        input_text="hi",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────────── (a) field tests

def test_identity_key_default_empty_string() -> None:
    """PipelineState.identity_key defaults to '' without any resolver."""
    state = _state()
    assert state.identity_key == ""


def test_identity_key_round_trips() -> None:
    """Explicitly-set identity_key is preserved on the constructed state."""
    state = _state(identity_key="owner-primary")
    assert state.identity_key == "owner-primary"


def test_identity_key_carried_by_evolve() -> None:
    """evolve() carries identity_key into the new instance when not overridden."""
    state = _state(identity_key="owner-primary")
    evolved = state.evolve(pipeline_step="triage")
    assert evolved.identity_key == "owner-primary"
    assert evolved.pipeline_step == "triage"


def test_identity_key_overridable_by_evolve() -> None:
    """evolve() can update identity_key."""
    state = _state(identity_key="")
    updated = state.evolve(identity_key="owner-primary")
    assert updated.identity_key == "owner-primary"


# ────────────────────────────────────────────────────────────────── (b) gateway resolution

def test_resolver_maps_known_handle_to_identity() -> None:
    """IdentityResolver.resolve returns the canonical identity for a known handle."""
    resolver = IdentityResolver({"owner-primary": ["telegram:123", "slack:U9"]})
    assert resolver.resolve("telegram:123") == "owner-primary"
    assert resolver.resolve("slack:U9") == "owner-primary"


def test_resolver_returns_handle_for_unknown_session() -> None:
    """Unmapped handle falls back to the handle itself (byte-identical behavior)."""
    resolver = IdentityResolver({"owner-primary": ["telegram:123"]})
    assert resolver.resolve("telegram:999") == "telegram:999"


def test_gateway_state_build_with_resolver_known_handle() -> None:
    """When resolver maps session_id, state.identity_key == identity key."""
    resolver = IdentityResolver({"owner-primary": ["telegram:123"]})
    session_id = "telegram:123"
    state = _state(session_id=session_id, identity_key=resolver.resolve(session_id))
    assert state.identity_key == "owner-primary"


def test_gateway_state_build_with_resolver_unknown_handle() -> None:
    """When resolver does not map session_id, identity_key == session_id (itself)."""
    resolver = IdentityResolver({"owner-primary": ["telegram:123"]})
    session_id = "telegram:999"
    state = _state(session_id=session_id, identity_key=resolver.resolve(session_id))
    # Unmapped: falls back to the handle itself
    assert state.identity_key == session_id


def test_empty_resolver_leaves_identity_key_as_handle() -> None:
    """An empty IdentityResolver (unconfigured deployment) resolves any handle to itself."""
    resolver = IdentityResolver({})
    handle = "telegram:555"
    assert resolver.resolve(handle) == handle


def test_services_has_identity_resolver_field() -> None:
    """StepServices has an identity_resolver field."""
    from stackowl.pipeline.services import StepServices

    svc = StepServices()
    # Should exist and default to None
    assert hasattr(svc, "identity_resolver")
    assert svc.identity_resolver is None


def test_services_identity_resolver_accepts_resolver() -> None:
    """StepServices.identity_resolver can be set to an IdentityResolver."""
    from stackowl.pipeline.services import StepServices

    resolver = IdentityResolver({"owner-primary": ["telegram:123"]})
    svc = StepServices(identity_resolver=resolver)
    assert svc.identity_resolver is resolver
    assert svc.identity_resolver.resolve("telegram:123") == "owner-primary"


# ──────────────────────────────────────────────── (c) _resolve_identity_key helper

class TestResolveIdentityKey:
    """Drive _resolve_identity_key — the REAL seam the orchestrator calls.

    These tests have teeth: if a build site hardcoded ``identity_key=""`` or
    returned ``session_id`` unconditionally, the mapped-handle case would fail.
    """

    def test_none_resolver_returns_empty_string(self) -> None:
        """Returns '' when no resolver is wired (consumers fall back to session_id)."""
        from stackowl.pipeline.services import StepServices
        from stackowl.startup.orchestrator import _resolve_identity_key

        svc = StepServices(identity_resolver=None)
        assert _resolve_identity_key(svc, "telegram:123") == ""

    def test_mapped_session_returns_identity_key(self) -> None:
        """A known handle resolves to its canonical identity_key."""
        from stackowl.pipeline.services import StepServices
        from stackowl.startup.orchestrator import _resolve_identity_key

        resolver = IdentityResolver({"owner-primary": ["telegram:123"]})
        svc = StepServices(identity_resolver=resolver)
        assert _resolve_identity_key(svc, "telegram:123") == "owner-primary"

    def test_unmapped_session_returns_session_id(self) -> None:
        """An unknown handle falls back to itself (byte-identical passthrough)."""
        from stackowl.pipeline.services import StepServices
        from stackowl.startup.orchestrator import _resolve_identity_key

        resolver = IdentityResolver({"owner-primary": ["telegram:123"]})
        svc = StepServices(identity_resolver=resolver)
        assert _resolve_identity_key(svc, "telegram:999") == "telegram:999"
