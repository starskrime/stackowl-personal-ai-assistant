"""skill_manage must be able to MEASURE every action, not just `create`.

FOUND BY AUDIT, not by a second bug report. Bakir hit this shape on 2026-08-16
with owl_build: a rename that WORKED was reported to him as "The capability that
failed: owl_build", because the overclaim gate DEFAULT-DENIES a durable
effect_class it cannot confirm, and verify() only covered the one action that
stamps `artifact_path`. Sweeping every durable-effect tool for the same shape
found skill_manage with the identical hole in five of its six actions
(edit / patch / delete / enable / disable).

WHY THE EXISTING RATCHET DID NOT CATCH IT. tests/tools/test_effect_class_
verification_ratchet.py asserts each effect-classed tool OVERRIDES verify() or
post_condition(). Both tools do. It measures the presence of a method, not
whether that method has an opinion about the actions the tool actually performs —
the same presence-vs-effect confusion the verification primitive exists to end.
These tests check the behaviour instead.

`create` keeps its artifact-path route and is covered by test_skill_manage's own
suite; this file is only about the five that used to return None.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from stackowl.pipeline.services import reset_services, set_services
from stackowl.tools.knowledge.skill_manage import SkillManageTool

pytestmark = pytest.mark.asyncio


class _Store:
    """Minimal skill store: returns whatever the test says the world contains."""

    def __init__(self, skill: object | None) -> None:
        self._skill = skill
        self.asked: list[tuple[str, ...]] = []

    async def get_many_by_name(self, names: tuple[str, ...]) -> list[object]:
        self.asked.append(names)
        return [self._skill] if self._skill is not None else []


def _skill(*, enabled: bool = True, path: str = "") -> SimpleNamespace:
    return SimpleNamespace(name="ops_helper", enabled=enabled, path=path)


async def _verdict(skill: object | None, args: dict) -> bool | None:
    services = SimpleNamespace(skill_store=_Store(skill))
    token = set_services(services)  # type: ignore[arg-type]
    try:
        return await SkillManageTool()._verify_by_action(  # noqa: SLF001
            args, started_at=time.time(),
        )
    finally:
        reset_services(token)


class TestDeleteIsConfirmedByAbsence:
    async def test_gone_is_true(self) -> None:
        assert await _verdict(None, {"action": "delete", "name": "ops_helper"}) is True

    async def test_still_there_is_false(self) -> None:
        """Claiming a delete is not achieving one."""
        assert await _verdict(_skill(), {"action": "delete", "name": "ops_helper"}) is False


class TestEnableDisableReadTheStoredFlag:
    async def test_enable_observes_enabled_true(self) -> None:
        assert await _verdict(_skill(enabled=True), {"action": "enable", "name": "ops_helper"}) is True

    async def test_enable_that_did_not_take_is_false(self) -> None:
        assert await _verdict(_skill(enabled=False), {"action": "enable", "name": "ops_helper"}) is False

    async def test_disable_observes_enabled_false(self) -> None:
        assert await _verdict(_skill(enabled=False), {"action": "disable", "name": "ops_helper"}) is True

    async def test_a_missing_skill_is_false_not_none(self) -> None:
        """The tool claimed to change something that is not there. That is a
        measured contradiction, not an unreadable world."""
        assert await _verdict(None, {"action": "enable", "name": "ops_helper"}) is False


class TestEditIsFreshnessNotPresence:
    async def test_a_skill_with_no_path_yields_no_opinion(self) -> None:
        """Presence alone cannot distinguish an edit that landed from one that did
        nothing, so without a path to check freshness against there is no honest
        verdict to give."""
        assert await _verdict(_skill(path=""), {"action": "edit", "name": "ops_helper"}) is None

    async def test_an_absent_file_is_false(self, tmp_path) -> None:
        missing = tmp_path / "nope" / "SKILL.md"
        assert await _verdict(
            _skill(path=str(missing)), {"action": "patch", "name": "ops_helper"},
        ) is False


class TestItDeclinesToGuess:
    async def test_no_store_yields_no_opinion(self) -> None:
        """An inability to look must never flip a real success into a failure —
        that is the mistake this whole method exists to prevent."""
        services = SimpleNamespace(skill_store=None)
        token = set_services(services)  # type: ignore[arg-type]
        try:
            verdict = await SkillManageTool()._verify_by_action(  # noqa: SLF001
                {"action": "delete", "name": "ops_helper"}, started_at=time.time(),
            )
        finally:
            reset_services(token)
        assert verdict is None

    async def test_missing_action_or_name_yields_no_opinion(self) -> None:
        assert await _verdict(_skill(), {}) is None
        assert await _verdict(_skill(), {"action": "delete"}) is None
