"""T10 — owned-skill suppression from the Relevant Skills block.

An owl's owned skills must never appear in the '## Relevant Skills' prompt
block, since they are already surfaced in the owned-playbook section at a
higher altitude. Duplicating them there causes weak-model repetition loops.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.steps import classify

pytestmark = pytest.mark.asyncio


class _Sk:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "d"
        self.when_to_use = "w"


class _Store:
    async def semantic_recall(self, vec: list, limit: int = 3) -> list:  # noqa: ARG002
        return [(_Sk("owned_one"), 0.9), (_Sk("other"), 0.8)]


class _EmbProv:
    async def embed(self, texts: list[str]) -> list[list[float]]:  # noqa: ARG002
        return [[0.1, 0.2, 0.3]]


class _EmbReg:
    def get(self) -> _EmbProv:
        return _EmbProv()


async def test_owned_skill_suppressed_from_relevant_block() -> None:
    """Owned skill name must not appear in the Relevant Skills block."""
    token = set_services(
        StepServices(skill_store=_Store(), embedding_registry=_EmbReg())
    )
    try:
        out = await classify._gather_relevant_skills("q", limit=3, owned={"owned_one"})
    finally:
        reset_services(token)

    assert "other" in out
    assert "owned_one" not in out


async def test_no_owned_filter_keeps_all() -> None:
    """When owned is None (default), all hits are included as before."""
    token = set_services(
        StepServices(skill_store=_Store(), embedding_registry=_EmbReg())
    )
    try:
        out = await classify._gather_relevant_skills("q", limit=3)
    finally:
        reset_services(token)

    assert "owned_one" in out
    assert "other" in out


async def test_empty_owned_keeps_all() -> None:
    """When owned is an empty set, all hits are included."""
    token = set_services(
        StepServices(skill_store=_Store(), embedding_registry=_EmbReg())
    )
    try:
        out = await classify._gather_relevant_skills("q", limit=3, owned=set())
    finally:
        reset_services(token)

    assert "owned_one" in out
    assert "other" in out


async def test_all_hits_owned_returns_empty_string() -> None:
    """When every hit is owned, the block collapses to '' (no orphan header)."""
    token = set_services(
        StepServices(skill_store=_Store(), embedding_registry=_EmbReg())
    )
    try:
        out = await classify._gather_relevant_skills(
            "q", limit=3, owned={"owned_one", "other"}
        )
    finally:
        reset_services(token)

    assert out == ""


async def test_relevant_block_names_a_loadable_tool_not_dead_cli_verb() -> None:
    """PA1 — the load hint must name the skill_view TOOL (callable mid-turn), not the
    /skill show CLI command (which the model cannot call). A dead verb here means the
    single best skill recommendation routes to a no-op."""
    token = set_services(
        StepServices(skill_store=_Store(), embedding_registry=_EmbReg())
    )
    try:
        out = await classify._gather_relevant_skills("q", limit=3)
    finally:
        reset_services(token)

    assert "/skill show" not in out  # the dead CLI verb must be gone
    assert "skill_view" in out  # the reachable load tool must be named
