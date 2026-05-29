"""E0-S3 — OwlAgentManifest.capability_profile field.

Additive, defaulted, backwards-compatible field holding toolset-group names for
DNA-gated presented-set selection (ADR-11). See E0-S3-capability-profile-field.md.
"""

from __future__ import annotations

from stackowl.owls.manifest import OwlAgentManifest


def _base(**extra: object) -> OwlAgentManifest:
    return OwlAgentManifest(
        name="scout", role="researcher", system_prompt="help",
        model_tier="fast", **extra,  # type: ignore[arg-type]
    )


def test_capability_profile_defaults_empty() -> None:
    """Existing manifests (no field set) still construct — backwards compatible."""
    m = _base()
    assert m.capability_profile == []


def test_capability_profile_accepts_toolset_groups() -> None:
    m = _base(capability_profile=["browser", "files", "memory"])
    assert m.capability_profile == ["browser", "files", "memory"]


def test_capability_profile_round_trips_through_dump_and_load() -> None:
    m = _base(capability_profile=["web", "scheduling"])
    dumped = m.model_dump()
    assert dumped["capability_profile"] == ["web", "scheduling"]
    restored = OwlAgentManifest(**dumped)
    assert restored.capability_profile == ["web", "scheduling"]


def test_capability_profile_absent_from_dump_round_trips_as_empty() -> None:
    m = _base()
    restored = OwlAgentManifest(**m.model_dump())
    assert restored.capability_profile == []
