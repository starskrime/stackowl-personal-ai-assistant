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

from stackowl.commands.owls_helpers import owl_is_persisted, persist_owl
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


# ---------------------------------------------------------------------------
# The grant path itself — `replace`, not `register`
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_grant_to_an_EXISTING_owl_actually_lands(tmp_db) -> None:  # noqa: ANN001
    """NO GRANT HAS EVER SURVIVED, and it was one word.

    `_grant` called `registry.register(updated)`. `register` guards against
    DUPLICATES — and a grant is by definition applied to an owl that ALREADY
    EXISTS, so it raised ManifestValidationError("duplicate owl name") on every
    single attempt. The surrounding `except` then rolled the durable write back,
    so the owl kept its old bounds and the operator's approval evaporated.

    Measured live on 2026-08-22, after `persist_owl` was made to raise and the
    failure stopped being silent: three consecutive grant attempts on `mailbutler`
    at 01:26:50, 01:34:31 and 01:45:01, every one rolled back on that exception.
    That is Bakir's "agents forget granted accesses ... never saved permanently".

    Every sibling mutation already had it right — `_edit` and both rebuild paths
    call `replace`, which documents itself as "the dual of register's duplicate
    guard". This one call site reached for the wrong verb.

    The test asserts the EFFECT: the tool is in the owl's bounds after the grant,
    and the change reached the store. Asserting only `result.success` would have
    passed against the broken version's rollback-then-report path.
    """
    from stackowl.authz import BoundsSpec
    from stackowl.infra.trace import TraceContext
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.streaming import StreamRegistry
    from stackowl.tools.consent import ConsentPolicy, TrustTier
    from stackowl.tools.meta.owl_build import OwlBuildTool
    from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

    narrow = BoundsSpec(tools=frozenset({"web_search"}))
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="mailbutler", role="assistant", system_prompt="s",
            model_tier="fast", bounds=narrow, creation_ceiling=narrow,
            tools=["web_search"],
        ),
        source_name="t",
    )

    token = set_services(StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=reg,
        consent_gate=ConsequentialActionGate(
            ConsentPolicy(tiers={"authority_widening": TrustTier.AUTO})
        ),
        stream_registry=StreamRegistry(),
        db_pool=tmp_db,
    ))
    trace = TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli",
        delegation_depth=0, owl_name="secretary",
    )
    try:
        result = await OwlBuildTool().execute(
            action="grant", name="mailbutler", explicit_tools=["read_file"],
        )
        # Read the store INSIDE the services scope — `owl_is_persisted` resolves
        # its db from the ambient services, so asserting after `reset_services`
        # reads no database and reports False for a row that is really there.
        landed = await owl_is_persisted("mailbutler")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert result.success, result.error
    held = set(reg.get("mailbutler").bounds.tools)
    assert "read_file" in held, f"the granted tool is not held: {sorted(held)}"
    assert "web_search" in held, "the grant must not drop what the owl already had"
    # THE EFFECT, not the report: the widened owl reached the store.
    assert landed, "the grant was reported successful but never reached the store"
