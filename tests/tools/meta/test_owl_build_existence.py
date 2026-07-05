"""Unit tests for owl_build's near-duplicate guard (existing_near_match).

Covers the cheap, deterministic name-token-overlap check that runs even with
no semantic embedder wired (StepServices() with embedding_registry=None,
its default) — see the live incident where 'research_brain' was never
flagged as a duplicate of 'Brain' by the semantic-only check.
"""
from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices
from stackowl.tools.meta.owl_build_existence import existing_near_match
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec


def _reg(*names: str) -> OwlRegistry:
    reg = OwlRegistry()
    for n in names:
        reg.register(
            OwlAgentManifest(
                name=n,
                role=n,
                system_prompt="p",
                model_tier="fast",
                origin="agent",
                created_by="secretary",
                creation_ceiling=BoundsSpec(tools=frozenset()),
                bounds=BoundsSpec(tools=frozenset()),
            ),
            source_name="t",
        )
    return reg


async def test_name_token_overlap_catches_duplicate_without_embedder() -> None:
    registry = _reg("Brain")
    spec = OwlBuildSpec(action="create", name="research_brain")
    services = StepServices()  # no embedder wired
    assert await existing_near_match(spec, registry, services) == "Brain"


async def test_name_token_overlap_does_not_false_positive_on_unrelated_name() -> None:
    registry = _reg("Brain")
    spec = OwlBuildSpec(action="create", name="weather_bot")
    services = StepServices()  # no embedder wired
    assert await existing_near_match(spec, registry, services) is None
