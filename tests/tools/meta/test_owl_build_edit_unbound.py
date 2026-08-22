"""Task 4 sub-part A — editing a builtin/human owl's tier/specialty works
directly (no agent-authority ratchet), preserving /owls edit's historical scope."""
from __future__ import annotations

import pytest

from stackowl.commands.owls_helpers import owl_is_persisted
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.meta.owl_build import OwlBuildTool

pytestmark = pytest.mark.asyncio


async def test_edit_builtin_owl_tier(tmp_db: DbPool) -> None:
    """THIS TEST WAS THE DEFECT IN MINIATURE.

    It passed `db_pool=None` and then asserted the IN-MEMORY registry — so it went
    green while the edit was never written anywhere. That is precisely what Bakir
    reported on 2026-08-22 ("agents forget granted accesses ... never saved
    permanently"): the registry says yes, the database never heard about it, and
    the change dies at the next restart.

    A real store is wired now, and the assertion reaches through to it.
    """
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="scout", role="research-scout", system_prompt="p",
            model_tier="fast", origin="builtin",
        ),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        result = await OwlBuildTool().execute(action="edit", name="scout", model_tier="powerful")
        assert result.success, result.error
        assert reg.get("scout").model_tier == "powerful"
        # AND IT ACTUALLY LANDED. Asserting the registry alone is what let the
        # original defect hide: an in-memory update is not a saved one.
        assert await owl_is_persisted("scout"), (
            "the edit was reported successful but never reached the store"
        )
    finally:
        reset_services(token)


async def test_edit_refuses_another_agents_owl() -> None:
    """A NON-ROOT owl may not edit an owl it did not create.

    The caller is now stated explicitly. This test used to rely on the DEFAULT
    caller, which is the secretary — and since 2026-08-22 she is the platform's
    root administrator ("Secretary should have access to everything"), so the
    default silently became the one caller exempt from the rule being tested. The
    protection is real and unchanged; only who it applies to needed saying out loud.
    """
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="helper", role="r", system_prompt="p", model_tier="fast",
            origin="agent", created_by="other_owl",
        ),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=None))
    trace = TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli",
        delegation_depth=0, owl_name="mailbutler",
    )
    try:
        result = await OwlBuildTool().execute(action="edit", name="helper", model_tier="powerful")
        assert not result.success
        assert "you may only modify owls you created" in result.error
    finally:
        TraceContext.reset(trace)
        reset_services(token)
