"""`/memory delete` is retired — `/memory forget` is the sole removal verb.

`delete` and `forget` were byte-identical implementations (same helper calls,
differing only in an internal actor tag used for logging). This drives out
the duplicate: `delete` no longer dispatches to anything and falls through to
the same usage/typo path as any other unknown sub-command, while `forget`
keeps working exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from tests._schema_template import seed_schema
from tests._story_6_7_helpers import (
    FakeBridge,
    make_settings,
    make_staged,
    make_state,
    no_test_mode_guard,  # noqa: F401
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
async def db(tmp_path: Path) -> DbPool:
    db_path = tmp_path / "mem_del_removed_test.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    yield pool
    await pool.close()


def _make_deps(bridge: FakeBridge, db: DbPool) -> CommandDeps:
    return CommandDeps(
        bridge=bridge,
        settings=make_settings(),
        db=db,
        event_bus=EventBus(),
    )


async def test_memory_delete_no_longer_a_subcommand(db: DbPool) -> None:
    """`/memory delete ... YES` no longer deletes anything — it's an unknown verb."""
    bridge = FakeBridge()
    fact = make_staged(fact_id="aabbccdd-0000-0000-0000-000000000009", content="still here")
    bridge.seed("staged", fact)

    deps = _make_deps(bridge, db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = (
        await CommandRegistry.instance().dispatch(
            "memory", "delete aabbccdd YES", make_state()
        )
    ).text

    # No longer a real subcommand — falls through to usage, same as any typo.
    assert "usage" in result.lower()
    assert "✓" not in result
    # Never reached the bridge.
    assert bridge.delete_calls == []


async def test_memory_forget_still_works(db: DbPool) -> None:
    """`/memory forget` is untouched by the `delete` removal.

    REPOINTED. This seeded a staged fact into a fake bridge and asserted the
    forget reached ``bridge.delete()``. D08.1 retargeted /memory at CURATED
    memory — `_forget` calls ``CuratedMemory.remove()`` and never touches the
    bridge — so the seeded fact could not match and the command correctly
    answered "No entry matching". The guarantee the test is named for is
    unchanged; only the store it lives in moved.
    """
    from stackowl.memory.curated import USER_TARGET, CuratedMemory

    CuratedMemory().add(USER_TARGET, "still works after the delete removal", "permanent")

    deps = _make_deps(FakeBridge(), db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = (
        await CommandRegistry.instance().dispatch(
            "memory", "forget still works after the delete removal", make_state()
        )
    ).text

    assert "✓" in result, result
    assert not any(
        "still works" in e.text for e in CuratedMemory().entries(USER_TARGET)
    ), "the entry survived a forget that reported success"
