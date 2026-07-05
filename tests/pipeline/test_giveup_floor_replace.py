import pytest
from stackowl.infra import recovery_context, tool_outcome_ledger as tol
from stackowl.pipeline.delivery_gate import surface_consequential_giveup_floor
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(text):
    return PipelineState(trace_id="t", session_id="s", input_text="send the email", channel="cli",
                         owl_name="secretary", pipeline_step="deliver",
                         responses=(ResponseChunk(content=text, is_final=False, chunk_index=0,
                                                  trace_id="t", owl_name="secretary"),))


@pytest.mark.asyncio
async def test_replaces_dressed_up_draft_with_honest_floor():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        s = _state("I have built the full agentic bridge for you. Here are the steps...")
        out = await surface_consequential_giveup_floor(s)
        delivered = "".join(c.content for c in out.responses)
        assert "built the full agentic bridge" not in delivered     # the excuse is GONE
        assert delivered.strip()                                     # floor is non-empty
        assert "send_email" in delivered or "could" in delivered.lower()  # honest floor names capability / couldn't
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_honest_floor_cites_real_error_text():
    """The floor's 'Technical detail:' must carry the tool's ACTUAL error string
    (e.g. a manifest validation failure), not a blank slot — the live incident
    this guards: owl_build failed with a real pydantic error but the floor
    rendered 'Technical detail:' with nothing after it."""
    token = tol.bind()
    try:
        tol.record_tool_outcome(
            name="owl_build", action_severity="consequential", success=False,
            error="Owl manifest validation failed [name]: exceeds 16 characters",
        )
        s = _state("Done — created the owl.")
        out = await surface_consequential_giveup_floor(s)
        delivered = "".join(c.content for c in out.responses)
        assert "Owl manifest validation failed [name]: exceeds 16 characters" in delivered
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_honest_floor_cites_real_error_text_from_snapshot():
    """Same guard as test_honest_floor_cites_real_error_text but via the
    REACT-7/F099 state snapshot path (what a real turn actually uses in
    production — execute._snapshot_consequential stamps this onto state
    instead of leaving the honesty decision on the live ledger)."""
    s = PipelineState(
        trace_id="t", session_id="s", input_text="create the owl", channel="cli",
        owl_name="secretary", pipeline_step="deliver",
        responses=(ResponseChunk(content="Done — created the owl.", is_final=False,
                                  chunk_index=0, trace_id="t", owl_name="secretary"),),
        consequential_failures=("owl_build",),
        consequential_failure_errors=(
            "Owl manifest validation failed [name]: exceeds 16 characters",
        ),
        consequential_snapshot_taken=True,
    )
    out = await surface_consequential_giveup_floor(s)
    delivered = "".join(c.content for c in out.responses)
    assert "Owl manifest validation failed [name]: exceeds 16 characters" in delivered


@pytest.mark.asyncio
async def test_no_replace_when_consequential_succeeded():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=True)
        s = _state("Done — sent the email.")
        out = await surface_consequential_giveup_floor(s)
        assert out.responses == s.responses
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_no_replace_when_no_consequential_attempt():
    token = tol.bind()
    try:
        s = _state("Here's the answer to your question.")
        out = await surface_consequential_giveup_floor(s)
        assert out.responses == s.responses
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_no_replace_when_substitution_bridged_the_gap():
    """Ledger shows an unachieved consequential outcome BUT a substitution recovery
    event is present — the capability gap was bridged, so the floor must NOT fire."""
    tol_token = tol.bind()
    rc_token = recovery_context.bind()
    try:
        # A consequential tool failed, no consequential success — raw ledger looks like give-up.
        tol.record_tool_outcome(name="browser_browse", action_severity="consequential", success=False)
        # A substitution recovery event says web_search bridged the gap.
        recovery_context.record_recovery(
            kind="substitution",
            failed="browser_browse",
            recovered_via="web_search",
            user_visible=True,
        )
        s = _state("I found it using an alternative search: sunny 24C.")
        out = await surface_consequential_giveup_floor(s)
        # The floor must NOT replace — the substitution already delivered the result.
        assert out.responses == s.responses, (
            "floor fired on a substitution-bridged turn — shared predicate did not guard it"
        )
    finally:
        tol.reset(tol_token)
        recovery_context.reset(rc_token)
