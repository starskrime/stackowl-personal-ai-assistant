"""D01.4 — an owl_build edit invalidates too, not just the slash command.

FOUND IN CLEANUP. Slice 2 filled the session_prompt_store gap in the partial
StepServices that owls_command builds, so this path COULD reach the store — and
then nothing called it. The seam existed and was unreachable, which is the
"registered but never wired" shape this repo has been caught by repeatedly.

It matters more here than for the slash command. owl_build is the SELF-EXTENSION
path: an owl that rewrites its own specialty mid-task and does not invalidate
keeps running on the persona it started the session with, so the autonomy loop
silently ignores its own change until 04:00.

Per the architect decision, an owl's change lands at the NEXT TURN boundary —
the in-flight turn finishes on the prompt it started with — which is exactly what
invalidation gives: the row is cleared now, the next turn cold-builds.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.sessions.prompt_store import SessionPromptStore
from stackowl.tools.meta.owl_build import OwlBuildTool

pytestmark = pytest.mark.asyncio

LANE = "owl:scout:cli:dm:d014ob"
RUN = "20260728_100000_d014obaa"


def _registry() -> OwlRegistry:
    registry = OwlRegistry.with_default_secretary()
    registry.register(
        OwlAgentManifest(
            name="scout", role="a test owl", system_prompt="Be helpful.",
            model_tier="fast", dna=OwlDNA(),
        )
    )
    return registry


async def _freeze(db: DbPool, owl: str) -> None:
    await SessionPromptStore(db).save(
        session_key=LANE, owl_name=owl, session_id=RUN,
        prompt_text=f"frozen prompt for {owl}", model_window=None,
    )


async def test_an_owl_build_edit_clears_the_frozen_prompt(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    registry = _registry()
    await _freeze(tmp_db, "scout")

    token = set_services(StepServices(
        owl_registry=registry, db_pool=tmp_db,
        session_prompt_store=SessionPromptStore(tmp_db),
    ))
    try:
        result = await OwlBuildTool().execute(
            action="edit", name="scout", specialty="a REWRITTEN specialty",
        )
    finally:
        reset_services(token)

    assert result.success, result.error
    assert await SessionPromptStore(tmp_db).load(
        session_key=LANE, owl_name="scout", session_id=RUN
    ) is None, (
        "owl_build edited the owl without clearing its frozen prompt — a "
        "self-extending owl would keep running on its old persona"
    )


async def test_an_owl_build_edit_survives_a_missing_store(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """I3 on the self-extension path — the edit must not fail over a cache concern.

    The mirror of the slash command's fail-open. It must also be LOUD: an owl
    that silently ignores its own change is indistinguishable from a bug, and
    this is the path where nobody is watching to notice.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    registry = _registry()

    # A StepServices with NO session_prompt_store — the partial shape that
    # existed everywhere before slice 2 filled the gap.
    token = set_services(StepServices(owl_registry=registry, db_pool=tmp_db))
    try:
        with caplog.at_level("ERROR"):
            result = await OwlBuildTool().execute(
                action="edit", name="scout", specialty="edited with no store",
            )
    finally:
        reset_services(token)

    assert result.success, result.error
    assert any("invalidate" in r.message.lower() for r in caplog.records), (
        "a failure to invalidate must be logged, never swallowed"
    )
