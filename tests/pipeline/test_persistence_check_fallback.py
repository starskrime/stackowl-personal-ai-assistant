"""W1.T3 — persistence-check fallback judge tier + all-turns enforcement.

The deliver-vs-giveup judge used to be a nested closure inside execute(), gated to
interactive depth-0 turns and backed by a SINGLE judge provider whose failure was
silently accepted. This suite drives the extracted module-level factory
:func:`build_persistence_check`:

  * a primary-judge failure falls through to a DIFFERENT fallback provider before
    failing open;
  * a primary "delivered" verdict still returns no directive (unchanged behaviour);
  * the checker is built even for a delegated / non-interactive turn (gate dropped).

The fake judges mirror the real provider interface ``judge_delivery`` calls —
``provider.complete(messages, model="")`` returning an object with a ``.content``
strict-JSON string (see ``stackowl.pipeline.persistence.judge_delivery``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from stackowl.infra import tool_outcome_ledger
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import build_persistence_check


@contextmanager
def _gate_open_on_failed_tool() -> Iterator[None]:
    """FR-10 — these tests exercise the judge/fallback-tier machinery directly, which
    now only runs when the gate is open. Bind the turn ledger with one FAILED,
    non-effectful outcome so condition (1) opens the gate without perturbing the
    PA2 consequential tally (only write/consequential severities feed that)."""
    token = tool_outcome_ledger.bind()
    tool_outcome_ledger.record_tool_outcome(
        name="probe", action_severity="read", success=False,
    )
    try:
        yield
    finally:
        tool_outcome_ledger.reset(token)


class _Completion:
    def __init__(self, content: str) -> None:
        self.content = content


class _RaisingJudge:
    """Primary judge model that errors on every call."""

    async def complete(self, *a: object, **k: object) -> _Completion:
        raise RuntimeError("judge down")


class _DeliveredJudge:
    """Judge that rules 'delivered' -> no directive."""

    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion('{"delivered": true, "reason": "ok"}')


class _GaveUpJudge:
    """Judge that rules 'gave up' -> directive."""

    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion('{"delivered": false, "reason": "stub"}')


class _EmptyJudge:
    """Judge whose model returns EMPTY content -> judge_delivery fails open
    (returns the JUDGE_ERROR_REASON sentinel), the 2026-06-23 reasoning-model
    truncation case."""

    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion("")


class _GaveUpThenEmptyJudge:
    """Real give-up on the first call, then EMPTY (fail-open) on the next — the
    live sequence: the judge ruled give-up, the turn was nudged, then the re-judge
    came back empty and 'failed open', erasing the give-up."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *a: object, **k: object) -> _Completion:
        self.calls += 1
        if self.calls == 1:
            return _Completion('{"delivered": false, "reason": "no evidence"}')
        return _Completion("")


class _GaveUpThenDeliveredJudge:
    """Give-up first, then a GENUINE delivered:true — the give-up was actually
    resolved, so the turn must be accepted (not held)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *a: object, **k: object) -> _Completion:
        self.calls += 1
        if self.calls == 1:
            return _Completion('{"delivered": false, "reason": "no evidence"}')
        return _Completion('{"delivered": true, "reason": "now grounded"}')


@pytest.fixture
def fake_services() -> StepServices:
    # No provider_registry needed — tests inject primary/fallback explicitly.
    return StepServices()


def _state(trace_id: str, text: str, *, interactive: bool, depth: int) -> PipelineState:
    return PipelineState(
        trace_id=trace_id,
        session_id="s-1",
        input_text=text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="execute",
        interactive=interactive,
        delegation_depth=depth,
    )


@pytest.fixture
def fake_state() -> PipelineState:
    return _state("t-interactive", "do the hard task", interactive=True, depth=0)


@pytest.fixture
def fake_state_delegated() -> PipelineState:
    return _state(
        "t-delegated", "do the delegated sub-task", interactive=False, depth=2
    )


@pytest.mark.asyncio
async def test_fallback_used_when_primary_raises(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    check = build_persistence_check(
        fake_state, fake_services, primary=_RaisingJudge(), fallback=_GaveUpJudge()
    )
    with _gate_open_on_failed_tool():  # FR-10 — open the gate (a failed tool ran)
        directive = await check("a give-up draft", ["browser_browse(failed)"])
    assert directive == PERSISTENCE_DIRECTIVE  # fallback consulted, ruled give-up


@pytest.mark.asyncio
async def test_primary_delivered_no_directive(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    check = build_persistence_check(
        fake_state, fake_services, primary=_DeliveredJudge()
    )
    with _gate_open_on_failed_tool():  # FR-10 — open the gate so the judge actually runs
        assert await check("a fine answer", ["shell(ok)"]) is None


@pytest.mark.asyncio
async def test_both_judges_raise_clean_turn_fail_open(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    # CLEAN turn (NO tool work): primary AND fallback raise -> final fail-open (None),
    # ordinary conversation is never blocked by a judge outage. The substantive-work
    # slice of this case is closed in test_pa2_* below.
    check = build_persistence_check(
        fake_state, fake_services, primary=_RaisingJudge(), fallback=_RaisingJudge()
    )
    assert await check("a plain reply", []) is None


@pytest.mark.asyncio
async def test_failopen_after_giveup_preserves_giveup(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """2026-06-23 break: once a real give-up is seen this turn, a later judge
    fail-open (empty output, both judges) must NOT ship the unvetted draft — it
    must preserve the give-up (nudge), never accept."""
    primary = _GaveUpThenEmptyJudge()
    check = build_persistence_check(
        fake_state, fake_services, primary=primary, fallback=_EmptyJudge()
    )
    with _gate_open_on_failed_tool():  # FR-10 — open the gate (a failed tool ran)
        # 1st check: real give-up -> nudge.
        assert await check("draft v1", ["browser_browse(failed)"]) == PERSISTENCE_DIRECTIVE
        # 2nd check: both judges empty (fail open) -> must STILL hold the give-up.
        assert await check("draft v2", ["browser_browse(failed)"]) == PERSISTENCE_DIRECTIVE


@pytest.mark.asyncio
async def test_genuine_delivered_after_giveup_is_accepted(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """A give-up that is genuinely resolved (parsed delivered:true) is accepted —
    preservation must not over-floor a legitimately-fixed answer."""
    check = build_persistence_check(
        fake_state, fake_services, primary=_GaveUpThenDeliveredJudge()
    )
    with _gate_open_on_failed_tool():  # FR-10 — open the gate (a failed tool ran)
        assert await check("draft v1", ["browser_browse(failed)"]) == PERSISTENCE_DIRECTIVE
        assert await check("draft v2 grounded", ["web_search(ok)"]) is None


class _RecordingRegistry:
    """Provider registry that records every tier requested via get_with_cascade."""

    def __init__(self, judge: object) -> None:
        self.tiers: list[str] = []
        self._judge = judge

    def get_with_cascade(self, tier: str) -> object:
        self.tiers.append(tier)
        return self._judge


class _SettingsStub:
    def __init__(self, judge_tier: str) -> None:
        self.judge_tier = judge_tier


@pytest.mark.asyncio
async def test_judge_resolves_standard_tier_by_default(
    fake_state: PipelineState,
) -> None:
    """Default (no settings): the delivery judge resolves the "standard" tier, NOT
    "fast" — the 2b fast tier was slow (thousands of think tokens) and ruled
    give-up unreliably."""
    reg = _RecordingRegistry(_DeliveredJudge())
    services = StepServices(provider_registry=reg)  # settings=None → default
    check = build_persistence_check(fake_state, services)
    with _gate_open_on_failed_tool():  # FR-10 — open the gate so the judge is resolved
        await check("a fine answer", ["shell(ok)"])
    assert reg.tiers, "judge tier was never resolved"
    assert reg.tiers[0] == "standard"


@pytest.mark.asyncio
async def test_judge_tier_overridable_via_settings(
    fake_state: PipelineState,
) -> None:
    """The judge tier is config-driven: settings.judge_tier wins."""
    reg = _RecordingRegistry(_DeliveredJudge())
    services = StepServices(
        provider_registry=reg, settings=_SettingsStub("powerful")
    )
    check = build_persistence_check(fake_state, services)
    with _gate_open_on_failed_tool():  # FR-10 — open the gate so the judge is resolved
        await check("a fine answer", ["shell(ok)"])
    assert reg.tiers[0] == "powerful"


@pytest.mark.asyncio
async def test_checker_built_for_delegated_turn(
    fake_state_delegated: PipelineState, fake_services: StepServices
) -> None:
    # Gate dropped: non-interactive / depth>0 STILL gets a checker (not None).
    check = build_persistence_check(
        fake_state_delegated, fake_services, primary=_DeliveredJudge()
    )
    assert check is not None
    with _gate_open_on_failed_tool():  # FR-10 — open the gate so the judge is resolved
        assert await check("a fine answer", ["shell(ok)"]) is None


# =========================================================================== #
# PA2 — close the residual fail-OPEN hole: a substantive NON-EFFECTFUL turn the
# judge never vetted (judge-error on every pass, no give-up flagged) must NOT ship
# an unvetted draft. It has no consequential-floor backstop, so fail CLOSED: nudge
# once. A CLEAN turn (no tools) and an EFFECTFUL turn (floor backstops it) still
# accept, so the fix neither blocks ordinary chat nor double-handles effectful work.
# =========================================================================== #


@pytest.mark.asyncio
async def test_pa2_substantive_uneffectful_unvetted_turn_nudges(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """THE HOLE: both judges fail to vet, no give-up was ever seen, but the turn ran
    substantive (non-effectful) tool work the judge never vetted. Pre-fix this
    silently accepted (returned None, shipping an unvetted draft). It must now nudge
    once toward an honest, grounded answer."""
    from stackowl.infra import tool_outcome_ledger

    token = tool_outcome_ledger.bind()  # bound turn, NO effectful outcomes recorded
    try:
        # A long read/research synthesis: read tool ran (substantive), nothing effectful.
        tool_outcome_ledger.record_tool_outcome(
            name="read_file", action_severity="read", success=True,
        )
        # FR-10 — with tools_tried non-empty and no failure, the gate would now skip
        # the judge before ever reaching this PA2 branch. Record one FAILED
        # non-effectful outcome to open the gate without perturbing the
        # consequential tally (only write/consequential severities feed it), so
        # this test still exercises the PA2 nudge-once branch it targets.
        tool_outcome_ledger.record_tool_outcome(
            name="probe", action_severity="read", success=False,
        )
        check = build_persistence_check(
            fake_state, fake_services, primary=_RaisingJudge(), fallback=_EmptyJudge()
        )
        directive = await check(
            "Here is a synthesis I never let anything verify.", ["read_file(ok)"]
        )
        assert directive == PERSISTENCE_DIRECTIVE
    finally:
        tool_outcome_ledger.reset(token)


@pytest.mark.asyncio
async def test_pa2_fires_at_most_once_across_reanswer_passes(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """`tools_tried` and the tally do not change between re-answer passes, so the PA2
    block must NOT re-fire each pass and drain the nudge budget on an already-honest
    draft. First unvetted-substantive pass → nudge; the second → accept (None)."""
    from stackowl.infra import tool_outcome_ledger

    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="read_file", action_severity="read", success=True,
        )
        # FR-10 — open the gate (see comment in the sibling test above) without
        # perturbing the consequential tally.
        tool_outcome_ledger.record_tool_outcome(
            name="probe", action_severity="read", success=False,
        )
        check = build_persistence_check(
            fake_state, fake_services, primary=_RaisingJudge(), fallback=_EmptyJudge()
        )
        first = await check("Honest synthesis, take 1.", ["read_file(ok)"])
        second = await check("Honest synthesis, take 2.", ["read_file(ok)"])
        assert first == PERSISTENCE_DIRECTIVE  # nudge ONCE
        assert second is None  # then accept — no budget-draining re-fire
    finally:
        tool_outcome_ledger.reset(token)


@pytest.mark.asyncio
async def test_pa2_clean_conversational_turn_still_ships(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """REGRESSION GUARD: a plain conversational turn (no tool work) with the judge
    down MUST still ship its draft — a judge outage must NOT start nudging ordinary
    chat into a loop. Empty tools_tried => nothing to deliver-or-give-up => accept."""
    from stackowl.infra import tool_outcome_ledger

    token = tool_outcome_ledger.bind()  # bound but EMPTY: no tools ran
    try:
        check = build_persistence_check(
            fake_state, fake_services, primary=_RaisingJudge(), fallback=_EmptyJudge()
        )
        assert await check("Sure, happy to chat about that.", []) is None
    finally:
        tool_outcome_ledger.reset(token)


@pytest.mark.asyncio
async def test_pa2_effectful_turn_left_to_consequential_floor(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """A turn that ran EFFECTFUL work is backstopped by the consequential give-up
    floor (has_consequential_snapshot), so the unvettable judge accepts here exactly
    as before — the fix must not double-handle effectful turns with a spurious nudge."""
    from stackowl.infra import tool_outcome_ledger

    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="write_file", action_severity="write", success=True,
        )
        # FR-10 — this test's whole point is exercising the unvettable-judge
        # accept path (_RaisingJudge/_EmptyJudge never resolve), so the gate
        # must be open. A second, FAILED, non-effectful outcome opens it
        # (condition 1) without perturbing the write/consequential tally the
        # PA2 backstop logic under test reads — mirrors
        # ``_gate_open_on_failed_tool`` above, inlined because this test
        # already owns its own bind/reset token.
        tool_outcome_ledger.record_tool_outcome(
            name="probe", action_severity="read", success=False,
        )
        check = build_persistence_check(
            fake_state, fake_services, primary=_RaisingJudge(), fallback=_EmptyJudge()
        )
        assert await check("Wrote the file.", ["write_file(ok)"]) is None
    finally:
        tool_outcome_ledger.reset(token)


# =========================================================================== #
# FR-10 — conditional give-up judge: the judge (and its fallback tier) only runs
# on a failed tool, an empty draft, or a refusal-shaped (0-tools + short) draft.
# A clean turn skips the judge entirely: no LLM call, no preg/provider touch.
# =========================================================================== #


class _SpyJudge:
    """Judge that records how many times it was called; always rules delivered."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *a: object, **k: object) -> _Completion:
        self.calls += 1
        return _Completion('{"delivered": true, "reason": "ok"}')


@pytest.mark.asyncio
async def test_fr10_clean_turn_skips_judge_entirely(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """(a) No failed tools, non-empty substantive draft, tools_tried non-empty ->
    the judge/provider mock is NEVER called and the checker returns None (the
    same value a 'delivered' verdict would have produced)."""
    spy = _SpyJudge()
    check = build_persistence_check(fake_state, fake_services, primary=spy)
    token = tool_outcome_ledger.bind()  # bound, but nothing failed this turn
    try:
        result = await check(
            "Done — the file was updated successfully.", ["write_file(ok)"]
        )
    finally:
        tool_outcome_ledger.reset(token)
    assert result is None
    assert spy.calls == 0, "clean turn must not invoke the judge"


@pytest.mark.asyncio
async def test_fr10_failed_tool_invokes_judge(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """(b) A failed tool call this turn (ledger-recorded) still invokes the judge."""
    spy = _SpyJudge()
    check = build_persistence_check(fake_state, fake_services, primary=spy)
    token = tool_outcome_ledger.bind()
    try:
        tool_outcome_ledger.record_tool_outcome(
            name="browser_browse", action_severity="read", success=False,
        )
        await check("I looked but could not find it.", ["browser_browse(failed)"])
    finally:
        tool_outcome_ledger.reset(token)
    assert spy.calls == 1, "a failed tool call this turn must invoke the judge"


@pytest.mark.asyncio
async def test_fr10_empty_draft_invokes_judge(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """(c) An empty draft still invokes the judge, even with tools_tried non-empty
    and no ledger failures."""
    spy = _SpyJudge()
    check = build_persistence_check(fake_state, fake_services, primary=spy)
    token = tool_outcome_ledger.bind()
    try:
        await check("   ", ["shell(ok)"])
    finally:
        tool_outcome_ledger.reset(token)
    assert spy.calls == 1, "an empty draft must invoke the judge"


@pytest.mark.asyncio
async def test_fr10_zero_tools_short_draft_invokes_judge(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """(d) Zero tools tried AND a short draft (< _SHORT_DRAFT_CHARS) — the
    refusal-shaped proxy — still invokes the judge."""
    spy = _SpyJudge()
    check = build_persistence_check(fake_state, fake_services, primary=spy)
    token = tool_outcome_ledger.bind()
    try:
        await check("Sorry, I can't do that.", [])
    finally:
        tool_outcome_ledger.reset(token)
    assert spy.calls == 1, "0 tools + short draft must invoke the judge"


@pytest.mark.asyncio
async def test_fr10_zero_tools_long_draft_skips_judge(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    """(e) Zero tools tried but a LONG draft (>= _SHORT_DRAFT_CHARS) and no
    failures — proves the length threshold gates condition (3), not just
    '0 tools always triggers'."""
    from stackowl.pipeline.steps.execute import _SHORT_DRAFT_CHARS

    spy = _SpyJudge()
    check = build_persistence_check(fake_state, fake_services, primary=spy)
    long_draft = "A " * _SHORT_DRAFT_CHARS  # well over the char threshold
    assert len(long_draft.strip()) >= _SHORT_DRAFT_CHARS
    token = tool_outcome_ledger.bind()
    try:
        result = await check(long_draft, [])
    finally:
        tool_outcome_ledger.reset(token)
    assert result is None
    assert spy.calls == 0, "0 tools + a long substantive draft must skip the judge"
