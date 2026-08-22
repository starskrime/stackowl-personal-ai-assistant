"""A tool inside the ceiling but missing from bounds must still be GRANTED.

BAKIR, 2026-08-22: "Platform still failing."

MEASURED on the live box that afternoon. `secretary` ran a grant against `Brain`:

    14:07:01  owl_build.execute: entry   {"action": "grant", "name": "Brain"}
    14:07:01  owl_build.execute: exit    {"success": true, "owl": "Brain", "op": "grant"}
    14:13:04  overclaim.detected         {"failed_capability": "owl_build"}

No `persist_owl: stored` line anywhere in the turn, and `Brain.updated_at` still
read 2026-08-16 — SIX DAYS stale — through a "successful" grant. The tool reported
success and the durable record was never touched.

THE CAUSE. `_grant` computed what the owl already holds from the CREATION CEILING
alone::

    held = set(ceiling.tools or ())
    adding = sorted(requested - held)
    if not adding: return self._ok("... already allowed to hold — nothing to grant.")

But effective authority is ``bounds ∩ ceiling``. A tool sitting in the ceiling and
absent from bounds therefore answered "already allowed to hold", returned SUCCESS,
and wrote nothing — while the owl went on being refused it on every single run.
`sysdesign` is refused `web_search` daily with `web_search` sitting in its ceiling;
granting it was a guaranteed no-op that reported success.

A write with no effect, wearing an affirmative message — the first of this
codebase's four recurring shapes, in the one tool whose entire job is to make a
capability real.

AND IT FRAMED THE HONEST COMPONENT. The overclaim gate default-denies a durable
effect it cannot verify, so it correctly refused to let the turn claim the grant —
then named `owl_build` the failed capability. The gate was right and the tool was
lying to it, which is why Bakir kept reading "The capability that failed:
owl_build" about calls that returned success.
"""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.commands.owls_helpers import owl_is_persisted
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.asyncio


def _services(reg: OwlRegistry, db: DbPool, *, tier: TrustTier) -> StepServices:
    return StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=reg,
        consent_gate=ConsequentialActionGate(
            ConsentPolicy(tiers={"authority_widening": tier})
        ),
        stream_registry=StreamRegistry(),
        db_pool=db,
    )


def _owl_with(bounds: set[str], ceiling: set[str]) -> OwlAgentManifest:
    """The live `sysdesign` shape: bounds strictly inside the ceiling."""
    return OwlAgentManifest(
        name="sysdesign", role="assistant", system_prompt="s", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset(bounds)),
        creation_ceiling=BoundsSpec(tools=frozenset(ceiling)),
        tools=sorted(bounds),
    )


async def _run_grant(reg: OwlRegistry, db: DbPool, tool: str, *, tier=TrustTier.AUTO):
    token = set_services(_services(reg, db, tier=tier))
    trace = TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli",
        delegation_depth=0, owl_name="secretary",
    )
    try:
        result = await OwlBuildTool().execute(
            action="grant", name="sysdesign", explicit_tools=[tool],
        )
        landed = await owl_is_persisted("sysdesign")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    return result, landed


async def test_the_live_case_web_search_in_ceiling_missing_from_bounds(
    tmp_db: DbPool,
) -> None:
    """THE DEFECT. Reported success, wrote nothing, owl still refused every run."""
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        _owl_with(bounds={"memory", "tool_search"},
                  ceiling={"memory", "tool_search", "web_search"}),
        source_name="t",
    )

    result, landed = await _run_grant(reg, tmp_db, "web_search")

    assert result.success, result.error
    assert "nothing to grant" not in result.output.lower(), (
        "the grant short-circuited on the CEILING: web_search is in the ceiling but "
        f"NOT in bounds, so there was real work to do. Output: {result.output!r}"
    )
    held = set(reg.get("sysdesign").bounds.tools)
    assert "web_search" in held, f"bounds were never widened: {sorted(held)}"
    assert {"memory", "tool_search"} <= held, "the grant dropped what it already had"
    assert landed, "reported success but the owl never reached the store"


async def test_a_within_ceiling_grant_does_NOT_require_the_operator(
    tmp_db: DbPool,
) -> None:
    """Already-delegated authority must not ask again.

    `authority_widening` is always-ask, so routing a within-ceiling widening through
    it means the platform asking for permission the operator already gave at mint
    time — exactly what made it unable to heal itself. `TrustTier.NEVER` stands in
    for "the operator said no / could not be reached": the grant must still land,
    because nothing is being widened beyond what he already approved.
    """
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        _owl_with(bounds={"memory"}, ceiling={"memory", "web_search"}),
        source_name="t",
    )

    result, landed = await _run_grant(reg, tmp_db, "web_search", tier=TrustTier.NEVER)

    assert result.success, f"a within-ceiling grant was blocked by consent: {result.error}"
    assert "web_search" in set(reg.get("sysdesign").bounds.tools)
    assert landed


async def test_a_grant_CROSSING_the_ceiling_STILL_ASKS_the_operator(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE SECURITY LINE, unchanged: crossing the ceiling still goes to the operator.

    Asserted by observing whether the consent path is INVOKED, rather than by
    configuring a tier to refuse. `ConsentPolicy.tiers` is keyed by TOOL NAME
    (`tiers.get(tool_name, ALWAYS_ASK)`), so a category key like
    "authority_widening" is never consulted — a denial test built on it would have
    proved nothing while looking green. Invocation is also the exact property this
    change touched: consent moved from unconditional to `if crossing`.
    """
    reg = OwlRegistry.with_default_secretary()
    reg.register(_owl_with(bounds={"memory"}, ceiling={"memory"}), source_name="t")

    asked: list[str] = []

    async def _spy(self, summary, name, *, category=None, reversible=False):  # noqa: ANN001
        asked.append(category or "")
        return "refused by the operator"

    monkeypatch.setattr(OwlBuildTool, "_consent_or_refuse", _spy, raising=True)
    result, _ = await _run_grant(reg, tmp_db, "shell")

    assert asked == ["authority_widening"], (
        f"a ceiling-crossing grant did not ask the operator: asked={asked}"
    )
    assert not result.success, "the operator refused and the grant proceeded anyway"
    assert "shell" not in set(reg.get("sysdesign").bounds.tools or ())


async def test_a_WITHIN_ceiling_grant_does_not_ask_at_all(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same line — and the reason self-healing works.

    `authority_widening` is always-ask. Routing a within-ceiling widening through it
    means asking for permission already given at mint time, which is precisely what
    left the platform unable to close its own capability gaps.
    """
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        _owl_with(bounds={"memory"}, ceiling={"memory", "web_search"}), source_name="t"
    )

    asked: list[str] = []

    async def _spy(self, summary, name, *, category=None, reversible=False):  # noqa: ANN001
        asked.append(category or "")
        return "refused by the operator"

    monkeypatch.setattr(OwlBuildTool, "_consent_or_refuse", _spy, raising=True)
    result, landed = await _run_grant(reg, tmp_db, "web_search")

    assert asked == [], f"a within-ceiling grant asked the operator: {asked}"
    assert result.success, result.error
    assert "web_search" in set(reg.get("sysdesign").bounds.tools)
    assert landed


async def test_a_genuinely_held_tool_is_still_a_no_op(tmp_db: DbPool) -> None:
    """The other jaw: when the owl really can already use it, do nothing.

    Widening on every call would churn a durable write per grant and make the audit
    trail useless for answering "who widened what, and when".
    """
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        _owl_with(bounds={"memory", "web_search"}, ceiling={"memory", "web_search"}),
        source_name="t",
    )

    result, _ = await _run_grant(reg, tmp_db, "web_search")

    assert result.success
    assert "nothing to grant" in result.output.lower(), result.output
