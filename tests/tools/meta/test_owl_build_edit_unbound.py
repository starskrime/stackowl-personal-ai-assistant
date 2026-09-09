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


# ---------------------------------------------------------------------------
# THE FOUR FIELDS THAT WERE NEVER TESTED, AND SO WERE BROKEN FOR FIVE MONTHS.
#
# The file above covers `model_tier` — the ONE field the writer's hand-written map
# and the verifier's `_EDIT_CHECKED_FIELDS` agreed on. `_edit_unbound` wrote
# `spec.specialty` into `system_prompt` while the verifier checked `role`, and
# ignored `boundaries`, `display_name` and `evolution_strategy` entirely. So every
# edit of a builtin owl except a tier change persisted an UNCHANGED manifest, returned
# "Updated owl 'X'.", and was correctly caught as an overclaim — which failed the task,
# retried, and BANNED `owl_build`, making it fail identically forever.
#
# Bakir, 2026-09-09: "Ehich is always and always failing fornlast 5 month."
# ---------------------------------------------------------------------------


def _builtin(reg: OwlRegistry, name: str = "friday") -> None:
    reg.register(
        OwlAgentManifest(
            name=name, role="primary-assistant", system_prompt="p",
            model_tier="fast", origin="builtin",
        ),
        source_name="t",
    )


async def test_editing_the_specialty_lands_on_the_record_AND_the_prompt(
    tmp_db: DbPool,
) -> None:
    """`role` is what the verifier checks; `system_prompt` is what the model READS.

    MEASURED 2026-09-09: `dna_injector` reads `boundaries`, `display_name`, `name`
    and `system_prompt` — and NOT `role`. Writing only `role` would satisfy the
    verifier while changing nothing the model sees, replacing an honest failure with
    a silent one. Both must land.
    """
    reg = OwlRegistry()
    _builtin(reg)
    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        result = await OwlBuildTool().execute(
            action="edit", name="friday", specialty="JARVIS-style butler",
        )
        assert result.success, result.error
        assert result.verified is not False, "the edit did not verify"
        assert reg.get("friday").role == "JARVIS-style butler"
        assert reg.get("friday").system_prompt == "JARVIS-style butler"
    finally:
        reset_services(token)


async def test_editing_boundaries_is_no_longer_dropped_on_the_floor(
    tmp_db: DbPool,
) -> None:
    """`boundaries` IS read by the prompt injector, and the writer ignored it."""
    reg = OwlRegistry()
    _builtin(reg)
    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        result = await OwlBuildTool().execute(
            action="edit", name="friday", boundaries="Calm, dry-witted. Never chatty.",
        )
        assert result.success, result.error
        assert result.verified is not False, "the edit did not verify"
        assert reg.get("friday").boundaries == "Calm, dry-witted. Never chatty."
    finally:
        reset_services(token)


async def test_an_edit_that_names_no_field_is_not_reported_as_an_update(
    tmp_db: DbPool,
) -> None:
    """THE OVERCLAIM AT THE ROOT OF IT.

    `rebuilt = current.model_copy(update=updates) if updates else current` persisted
    an untouched manifest and returned "Updated owl 'X'." — a lie the tool did not
    have to tell, which the verifier then caught and turned into a permanent ban.
    """
    reg = OwlRegistry()
    _builtin(reg)
    token = set_services(StepServices(owl_registry=reg, db_pool=tmp_db))
    try:
        result = await OwlBuildTool().execute(action="edit", name="friday")
        assert not result.success, "an edit that changed nothing reported success"
        assert "editable" in (result.error or "").lower() or "field" in (result.error or "").lower()
    finally:
        reset_services(token)


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_the_writer_reads_the_SAME_map_the_verifier_checks() -> None:
    """One map, both sides. Two maps is how this survived five months."""
    import inspect

    from stackowl.tools.meta import owl_build

    src = inspect.getsource(owl_build.OwlBuildTool._edit_unbound)  # noqa: SLF001
    assert "_EDIT_CHECKED_FIELDS" in src, (
        "the writer has its own field list again — it will drift from the verifier"
    )
    assert 'updates["system_prompt"] = spec.specialty' not in src, (
        "the old two-field hand map is back"
    )
