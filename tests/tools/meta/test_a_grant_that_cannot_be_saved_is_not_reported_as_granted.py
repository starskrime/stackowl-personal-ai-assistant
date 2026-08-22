"""A grant that cannot be persisted must NOT be reported as granted.

BAKIR, 2026-08-22: "Somehow agents and platform forget granted accesses, looks
like it is never saved permanently." And earlier the same night: "Agent losses
granted tool access on next run."

THE DEFECT. `persist_owl` returned ``bool`` and documented itself as never
raising, so that a failed write "must still be reported honestly by its caller,
not crash the turn". The intent was right; a bool is ignorable, and ALL SIX call
sites ignored it. Five of them were already wrapped in ``try`` + ``restore_owl``
rollback — written as though it raised. The callers were correct and the function
was lying to them.

So on a persistence failure `owl_build._grant` widened the IN-MEMORY registry,
logged "authority WIDENED with the user's approval", returned success — and the
grant lived exactly as long as the process. Which is precisely "forgets granted
accesses on the next run".

This is the first of this codebase's four recurring shapes in its purest form: a
write with no reader. The function measured its own effect and returned it, and
nobody read the answer.
"""

from __future__ import annotations

import pytest

from stackowl.commands.owls_helpers import persist_owl
from stackowl.exceptions import OwlPersistError
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.services import StepServices, reset_services, set_services


def _manifest(name: str = "mailbutler") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="assistant", system_prompt="s", model_tier="fast",
    )


@pytest.mark.asyncio
async def test_an_unpersistable_grant_RAISES_rather_than_returning_a_lie() -> None:
    """With no db wired the write cannot land, and that must be unignorable.

    The old contract returned False here. Every caller dropped it on the floor,
    which is how a grant could be confirmed to the user and lost at the next
    restart.
    """
    token = set_services(StepServices())  # no db_pool
    try:
        with pytest.raises(OwlPersistError) as caught:
            await persist_owl(_manifest())
    finally:
        reset_services(token)

    assert caught.value.owl_name == "mailbutler"
    assert "no db" in caught.value.reason.lower(), caught.value.reason


@pytest.mark.asyncio
async def test_the_failure_names_the_owl_so_the_operator_can_act() -> None:
    """A persistence error that does not say WHICH owl is a page the operator
    cannot act on at 2am."""
    token = set_services(StepServices())
    try:
        with pytest.raises(OwlPersistError) as caught:
            await persist_owl(_manifest("headhunter"))
    finally:
        reset_services(token)
    assert "headhunter" in str(caught.value)


@pytest.mark.asyncio
async def test_a_successful_persist_still_returns_True(tmp_db) -> None:  # noqa: ANN001
    """The other jaw: the happy path must be unchanged, so a caller that wants to
    assert the effect still can. A fix that made every persist raise would be a
    worse defect than the one it replaced."""
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        assert await persist_owl(_manifest("scout")) is True
    finally:
        reset_services(token)
