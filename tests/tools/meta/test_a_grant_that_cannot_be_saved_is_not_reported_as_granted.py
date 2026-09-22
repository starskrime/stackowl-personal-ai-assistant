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
async def test_the_tool_reports_the_grant_as_PENDING_never_landed_inline(
    tmp_db,  # noqa: ANN001
) -> None:
    """Story 4.9 — ``owls.build.grant`` is ``severity="consequential"``, which
    ALWAYS parks for owner step-up (AC2), even for a request the tool-level
    ``authority_widening`` consent already auto-approved. So the tool-level
    call itself must report the grant as PENDING and must NOT widen the
    owl's bounds inline — the real land-it-or-roll-it-back fix (below) now
    lives in the command handler, exercised once it actually runs."""
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
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert result.success, result.error
    assert "pending" in result.output.lower()
    held = set(reg.get("mailbutler").bounds.tools)
    assert "read_file" not in held, "the grant widened bounds before owner step-up"


@pytest.mark.asyncio
async def test_the_grant_handler_replaces_not_registers_an_EXISTING_owl(
    tmp_db,  # noqa: ANN001
) -> None:
    """NO GRANT HAS EVER SURVIVED, and it was one word.

    `_grant` used to call `registry.register(updated)`. `register` guards
    against DUPLICATES — and a grant is by definition applied to an owl that
    ALREADY EXISTS, so it raised ManifestValidationError("duplicate owl
    name") on every single attempt. The surrounding `except` then rolled the
    durable write back, so the owl kept its old bounds and the operator's
    approval evaporated.

    Measured live on 2026-08-22, after `persist_owl` was made to raise and the
    failure stopped being silent: three consecutive grant attempts on `mailbutler`
    at 01:26:50, 01:34:31 and 01:45:01, every one rolled back on that exception.
    That is Bakir's "agents forget granted accesses ... never saved permanently".

    Story 4.9 moved the actual persist+register into
    `owl_build_commands.py::_grant_handler` — this test exercises THAT
    handler directly (the layer the fix now lives in, since the tool itself
    always parks before reaching it — see the sibling test above).
    """
    from stackowl.authz import BoundsSpec
    from stackowl.commands.spec.context import CommandContext
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.streaming import StreamRegistry
    from stackowl.tools.meta.owl_build_commands import OwlManifestPayload, _grant_handler
    from stackowl.tools.registry import ToolRegistry

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
    widened = OwlAgentManifest(
        name="mailbutler", role="assistant", system_prompt="s",
        model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({"web_search", "read_file"})),
        creation_ceiling=BoundsSpec(tools=frozenset({"web_search", "read_file"})),
        tools=["read_file", "web_search"],
    )

    token = set_services(StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=reg,
        stream_registry=StreamRegistry(),
        db_pool=tmp_db,
    ))
    try:
        outcome = await _grant_handler(
            OwlManifestPayload(manifest=widened, actor="secretary"),
            CommandContext(
                command_id="cmd-1", command_type="owls.build.grant",
                requester_kind="owner",
            ),
        )
        landed = await owl_is_persisted("mailbutler")
    finally:
        reset_services(token)

    assert outcome.success, outcome.error
    held = set(reg.get("mailbutler").bounds.tools)
    assert "read_file" in held, f"the granted tool is not held: {sorted(held)}"
    assert "web_search" in held, "the grant must not drop what the owl already had"
    # THE EFFECT, not the report: the widened owl reached the store.
    assert landed, "the grant was reported successful but never reached the store"
