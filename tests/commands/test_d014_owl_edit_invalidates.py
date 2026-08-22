"""D01.4 — editing an owl clears its frozen prompt, asserted from the COMMAND.

WHY FROM THE COMMAND AND NOT THE STORE. tests/sessions/test_prompt_store.py
already proves invalidate_owl() clears the right rows. That proves the mechanism
and NOTHING about whether anything calls it — a wiring omission would leave every
one of those tests green while the user's edit silently did nothing until 04:00.
That failure shape has already bitten this program twice: D01.2's cleanup stage
found the stream path marked but never instrumented, and its test stage found the
whole marker layer proven on a code path production does not take.

So these drive the real command and assert the row is gone.
"""

from __future__ import annotations

import pytest

from stackowl.commands.owls_command import OwlsCommand
from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.sessions.prompt_store import SessionPromptStore

pytestmark = pytest.mark.asyncio

LANE = "owl:scout:telegram:dm:72055773"
OTHER_LANE = "owl:scout:cli:dm:1"
RUN = "20260728_040000_d014aaaa"


def _owl(name: str, role: str = "a test owl") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role=role, system_prompt="Be helpful and accurate.",
        model_tier="fast", dna=OwlDNA(),
    )


def _registry() -> OwlRegistry:
    # NOT the secretary: _edit refuses to edit it ("mandatory and cannot be
    # edited"), so a secretary-based test would pass without invalidating
    # anything at all.
    registry = OwlRegistry.with_default_secretary()
    registry.register(_owl("scout"))
    return registry


async def _freeze(db: DbPool, lane: str, owl: str) -> None:
    await SessionPromptStore(db).save(
        session_key=lane, owl_name=owl, conversation_id=RUN,
        prompt_text=f"frozen prompt for {owl}", model_window=None,
    )


async def test_editing_an_owl_clears_its_frozen_prompt(tmp_db: DbPool) -> None:
    """THE user-visible promise: a change you just made reaches the next turn."""
    registry = _registry()
    await _freeze(tmp_db, LANE, "scout")

    command = OwlsCommand(owl_registry=registry, db=tmp_db)
    reply = await command._edit("scout --role 'a RENAMED test owl'")  # noqa: SLF001

    assert reply.startswith("✓"), reply
    store = SessionPromptStore(tmp_db)
    assert await store.load(session_key=LANE, owl_name="scout", conversation_id=RUN) is None, (
        "the edit did not clear the frozen prompt — it would stay invisible "
        "until the session rolled over"
    )


async def test_editing_an_owl_clears_it_on_every_lane(tmp_db: DbPool) -> None:
    registry = _registry()
    await _freeze(tmp_db, LANE, "scout")
    await _freeze(tmp_db, OTHER_LANE, "scout")

    command = OwlsCommand(owl_registry=registry, db=tmp_db)
    await command._edit("scout --role 'again'")  # noqa: SLF001

    store = SessionPromptStore(tmp_db)
    assert await store.load(session_key=LANE, owl_name="scout", conversation_id=RUN) is None
    assert await store.load(session_key=OTHER_LANE, owl_name="scout", conversation_id=RUN) is None


async def test_editing_one_owl_leaves_another_owls_prompt_alone(tmp_db: DbPool) -> None:
    registry = _registry()
    registry.register(_owl("researcher"))
    await _freeze(tmp_db, LANE, "scout")
    await _freeze(tmp_db, LANE, "researcher")

    command = OwlsCommand(owl_registry=registry, db=tmp_db)
    await command._edit("scout --role 'only scout changed'")  # noqa: SLF001

    store = SessionPromptStore(tmp_db)
    assert await store.load(session_key=LANE, owl_name="scout", conversation_id=RUN) is None
    assert await store.load(
        session_key=LANE, owl_name="researcher", conversation_id=RUN
    ) is not None, "editing scout must not clear researcher's prompt"


async def test_an_edit_with_no_database_still_succeeds(caplog: pytest.LogCaptureFixture) -> None:
    """I3 — the edit persisted; a missing store must never fail the command.

    It must also not be SILENT: an invalidation that cannot run means the change
    waits for rollover, and that is exactly the kind of thing that presents to a
    user as "my edit did nothing".
    """
    command = OwlsCommand(owl_registry=_registry(), db=None)
    with caplog.at_level("ERROR"):
        reply = await command._edit("scout --role 'no db here'")  # noqa: SLF001

    assert reply.startswith("✓"), reply
    assert any("invalidate" in r.message.lower() for r in caplog.records), (
        "a failure to invalidate must be logged, not swallowed"
    )
