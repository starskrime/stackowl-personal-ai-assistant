"""PrincipalStore — idempotent default + CRUD (Pass 1)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID, Principal
from stackowl.tenancy.store import PrincipalStore


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "principals.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_default_seeded_by_migration(pool: DbPool) -> None:
    store = PrincipalStore(pool)
    default = await store.get(DEFAULT_PRINCIPAL_ID)
    assert default is not None
    assert default.principal_type == "user"
    assert default.display_name == "Default Owner"


async def test_ensure_default_is_idempotent(pool: DbPool) -> None:
    store = PrincipalStore(pool)
    await store.ensure_default()
    await store.ensure_default()
    rows = await pool.fetch_all(
        "SELECT COUNT(*) AS n FROM principals WHERE principal_id = ?",
        (DEFAULT_PRINCIPAL_ID,),
    )
    assert int(str(rows[0]["n"])) == 1


async def test_create_get_list(pool: DbPool) -> None:
    store = PrincipalStore(pool)
    team = Principal(
        principal_id="principal-team-1",
        principal_type="team",
        display_name="Platform Team",
        created_at=datetime.now(tz=UTC),
    )
    await store.create(team)

    got = await store.get("principal-team-1")
    assert got is not None
    assert got.principal_type == "team"
    assert got.display_name == "Platform Team"

    ids = {p.principal_id for p in await store.list()}
    assert {DEFAULT_PRINCIPAL_ID, "principal-team-1"} <= ids


async def test_get_miss_returns_none(pool: DbPool) -> None:
    store = PrincipalStore(pool)
    assert await store.get("principal-nope") is None


async def test_create_duplicate_raises(pool: DbPool) -> None:
    store = PrincipalStore(pool)
    dup = Principal(
        principal_id=DEFAULT_PRINCIPAL_ID,
        principal_type="user",
        display_name="dup",
        created_at=datetime.now(tz=UTC),
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await store.create(dup)
