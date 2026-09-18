"""``runtime.hello`` -- Spec 2.3's gateway/core Hello compatibility check.

``evaluate_hello`` is pure/sync and run identically on both sides over the
SAME two Hello payloads; ``build_local_hello`` computes a real, fresh Hello
against a migrated database; ``react_to_core_hello_verdict`` is the core's
pure/sync reaction to its own verdict, kept separate from the orchestrator
so it is directly unit-testable.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.db.pool import DbPool
from stackowl.ipc.frames import HelloFrame
from stackowl.runtime.hello import (
    HelloVerdict,
    build_local_hello,
    evaluate_hello,
    react_to_core_hello_verdict,
)

pytestmark = pytest.mark.asyncio


def _hello(protocol_version: int = 2, highest_migration: int = 5, digest: str = "abc") -> HelloFrame:
    return HelloFrame(
        sender_pid=1,
        protocol_version=protocol_version,
        highest_migration=highest_migration,
        registry_digest=digest,
    )


class TestEvaluateHelloTruthTable:
    def test_matching_hellos_are_compatible(self) -> None:
        local = _hello()
        remote = _hello()
        verdict = evaluate_hello(local=local, remote=remote, local_is_core=True)
        assert verdict == HelloVerdict(compatible=True, older=None)

    def test_lower_protocol_version_is_older_from_its_own_side(self) -> None:
        # The CORE evaluates: its own protocol_version is lower -> "local" is older.
        local = _hello(protocol_version=1)
        remote = _hello(protocol_version=2)
        verdict = evaluate_hello(local=local, remote=remote, local_is_core=True)
        assert verdict.compatible is False
        assert verdict.older == "local"

        # The GATEWAY evaluates the SAME two payloads (roles swapped): the
        # core (now `remote`) is the one with the lower protocol_version.
        verdict2 = evaluate_hello(local=remote, remote=local, local_is_core=False)
        assert verdict2.compatible is False
        assert verdict2.older == "remote"

    def test_lower_highest_migration_is_older_when_protocol_version_matches(self) -> None:
        local = _hello(highest_migration=3)
        remote = _hello(highest_migration=9)
        verdict = evaluate_hello(local=local, remote=remote, local_is_core=True)
        assert verdict.compatible is False
        assert verdict.older == "local"

    def test_digest_only_mismatch_blames_the_gateway_from_the_cores_side(self) -> None:
        """protocol_version and highest_migration agree; only the registry
        digest differs -- the GATEWAY is the conventionally-older side. From
        the CORE's own evaluation (`local_is_core=True`), the gateway is
        `remote`."""
        core_hello = _hello(digest="core-digest")
        gateway_hello = _hello(digest="gateway-digest")
        verdict = evaluate_hello(local=core_hello, remote=gateway_hello, local_is_core=True)
        assert verdict.compatible is False
        assert verdict.older == "remote"

    def test_digest_only_mismatch_blames_the_gateway_from_its_own_side(self) -> None:
        """The SAME two payloads, evaluated by the gateway itself
        (`local_is_core=False`, so `local` IS the gateway) -> "local" is older."""
        core_hello = _hello(digest="core-digest")
        gateway_hello = _hello(digest="gateway-digest")
        verdict = evaluate_hello(local=gateway_hello, remote=core_hello, local_is_core=False)
        assert verdict.compatible is False
        assert verdict.older == "local"

    def test_protocol_version_mismatch_is_checked_before_migration(self) -> None:
        """Both protocol_version AND highest_migration differ -- the ordering
        rule says protocol_version wins first."""
        local = _hello(protocol_version=1, highest_migration=99)
        remote = _hello(protocol_version=2, highest_migration=1)
        verdict = evaluate_hello(local=local, remote=remote, local_is_core=True)
        assert verdict.older == "local"  # protocol_version, not migration, decided it


class TestBuildLocalHello:
    async def test_builds_a_real_hello_against_a_migrated_db(self, tmp_db: DbPool) -> None:
        hello = await build_local_hello(sender_pid=4242, db_pool=tmp_db)
        assert hello.sender_pid == 4242
        assert hello.highest_migration > 0  # the template carries every real migration
        assert hello.registry_digest  # non-empty

    async def test_is_stable_across_repeat_calls_against_the_same_db(
        self, tmp_db: DbPool
    ) -> None:
        first = await build_local_hello(sender_pid=1, db_pool=tmp_db)
        second = await build_local_hello(sender_pid=1, db_pool=tmp_db)
        assert first.highest_migration == second.highest_migration
        assert first.registry_digest == second.registry_digest

    async def test_zero_migrations_reports_zero_not_an_error(self, tmp_path) -> None:
        """An empty/fresh database (no migrations applied) reports 0, not a
        crash -- `schema_migrations` exists in every real install (the
        runner creates it before applying anything), but this pins the
        no-rows branch defensively."""
        db_path = tmp_path / "empty.db"
        pool = DbPool(db_path=db_path)
        await pool.open()
        try:
            await pool.execute(
                "CREATE TABLE schema_migrations (version TEXT NOT NULL UNIQUE)"
            )
            hello = await build_local_hello(sender_pid=1, db_pool=pool)
            assert hello.highest_migration == 0
        finally:
            await pool.close()


class TestReactToCoreHelloVerdict:
    """Both mismatch outcomes set `stop_event`, never `restart_event`.

    `restart_event` would drive the existing quiesce -> teardown -> os.execv
    self-restart, which replaces the process image WITHOUT the OS reporting
    an exit (same PID) -- `_supervise_core`'s `_MAX_CONSECUTIVE_HELLO_MISMATCHES`
    stand-down check only runs after `_wait_for_exit_or_stall` returns, which
    an in-place `os.execv` never triggers. Routing BOTH outcomes through
    `stop_event` means the process actually exits, so `_supervise_core`'s
    crash-respawn loop (which DOES observe a real exit) is the one place
    bounding every Hello-mismatch retry.
    """

    def test_older_local_sets_stop_event(self) -> None:
        stop_event = asyncio.Event()
        react_to_core_hello_verdict(
            HelloVerdict(compatible=False, older="local"), stop_event=stop_event,
        )
        assert stop_event.is_set() is True

    def test_older_remote_sets_stop_event(self) -> None:
        stop_event = asyncio.Event()
        react_to_core_hello_verdict(
            HelloVerdict(compatible=False, older="remote"), stop_event=stop_event,
        )
        assert stop_event.is_set() is True

    def test_compatible_is_a_no_op(self) -> None:
        stop_event = asyncio.Event()
        react_to_core_hello_verdict(
            HelloVerdict(compatible=True, older=None), stop_event=stop_event,
        )
        assert stop_event.is_set() is False
