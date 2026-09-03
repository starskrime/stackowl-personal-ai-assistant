"""An archived skill must not be presented, not even to the owl that owns it.

``_NOT_ARCHIVED`` describes itself as "the single place that decision is
expressed, so a new retrieval path cannot forget it". Measured 2026-09-03: it
appears in TWO of the three retrieval paths. ``get_many_by_name`` — the one that
serves an owl's OWN skills — did not have it, so a skill the curator had retired
stayed fully presented to every owl that owned it, tool pins included. Retirement
is this platform's single terminal state; if it does not hold on every read, the
curator is advisory.

LATENT, THEN IMMINENT, and both halves are recorded because the first alone would
be a claim of harm that had not happened: ZERO archived skills were owned when
this was fixed. It was hours from mattering — five superseded duplicates were
owned by 17 rows and were due to be archived on the next curator pass.
"""

from __future__ import annotations

import pytest

from stackowl.skills.store import SkillIndexStore
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


async def _seed(pool, name: str, *, state: str = "active") -> None:  # noqa: ANN001
    import json
    import time

    await pool.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use,"
        " body_text, manifest_json, n_executions, lifecycle_state, owner_id,"
        " loaded_at, updated_at) VALUES (?, 'learned', ?, 'd', 'w', 'b', ?, 0, ?, ?, ?, ?)",
        (name, f"/skills/learned/{name}", json.dumps({"name": name}), state,
         DEFAULT_PRINCIPAL_ID, time.time(), time.time()),
    )


async def test_an_ARCHIVED_owned_skill_is_not_returned(tmp_db) -> None:  # noqa: ANN001
    store = SkillIndexStore(tmp_db)
    await _seed(tmp_db, "incident_shell", state="active")
    await _seed(tmp_db, "incident_shell_stop", state="archived")

    got = {sk.name for sk in await store.get_many_by_name(
        ("incident_shell", "incident_shell_stop"),
    )}

    assert got == {"incident_shell"}, (
        "an archived skill came back through the ownership read — the curator's "
        "one terminal state does not hold, and the owl is still being taught a "
        "lesson the platform retired"
    )


async def test_an_ACTIVE_owned_skill_is_STILL_returned(tmp_db) -> None:  # noqa: ANN001
    """The control. A filter that returns nothing passes the test above while
    silently removing every skill an owl has."""
    store = SkillIndexStore(tmp_db)
    await _seed(tmp_db, "incident_shell")
    await _seed(tmp_db, "incident_web_fetch")

    got = {sk.name for sk in await store.get_many_by_name(
        ("incident_shell", "incident_web_fetch"),
    )}

    assert got == {"incident_shell", "incident_web_fetch"}
