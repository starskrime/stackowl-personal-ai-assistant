"""D05.3 — proof the gate is WIRED into presentation, not merely correct.

tests/infra/test_capabilities.py proves the registry behaves. It proves nothing
about presentation consulting it. D05.2 shipped a suite with exactly that hole:
mutation M1 disabled the memo in execute.py and 19 module tests stayed green
while the platform rebuilt the array every turn.

So this file drives the REAL path — real ToolRegistry, real ToolPresentation,
real budgeter, only the AI provider mocked — and asserts on what the provider
actually received.

MUTATION M3 (run at validate): make `_capability_ok` in presentation.py return
True unconditionally. `test_an_unavailable_capability_removes_its_tools_from_the
_provider_array` must FAIL and every test in tests/infra/test_capabilities.py
must still PASS.
"""

from __future__ import annotations

import pytest

from stackowl.infra import capabilities, presented_tools


class _DownBrowser:
    available = False
    unavailable_reason = "missing libx11-xcb"
    remedy = "sudo apt install libx11-xcb1"

    def register_on_recycled(self, cb):
        self._cb = cb


class _UpBrowser:
    available = True
    unavailable_reason = None

    def register_on_recycled(self, cb):
        self._cb = cb


@pytest.fixture(autouse=True)
def _clean():
    capabilities.clear()
    presented_tools.clear()
    yield
    capabilities.clear()
    presented_tools.clear()


class _FakeProvider:
    protocol = "anthropic"

    def __init__(self, sink):
        self._sink = sink

    async def complete_with_tools(
        self, user_text, system_text, tool_schemas, tool_dispatcher,
        max_iterations=8, history=None, **_kw,
    ):
        self._sink.append([s["name"] for s in tool_schemas])
        return "ok", []


class _FakeProviderRegistry:
    def __init__(self, p):
        self._p = p

    def get(self, name):
        return self._p

    def get_by_tier(self, tier):
        return self._p


async def _run(sink, *, session_key: str) -> None:
    """One real turn: real registry, real presentation, real budgeter."""
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import execute
    from stackowl.tools.registry import ToolRegistry

    services = StepServices(
        provider_registry=_FakeProviderRegistry(_FakeProvider(sink)),
        tool_registry=ToolRegistry.with_defaults(),
    )
    token = set_services(services)
    try:
        state = PipelineState(
            trace_id=f"t-{session_key}", session_key=session_key,
            input_text="browse a page", channel="cli", owl_name="secretary",
            pipeline_step="execute", system_prompt="SYS",
            model_window=200_000,
        )
        await execute.run(state)
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_an_unavailable_capability_removes_its_tools_from_the_provider_array():
    """THE WIRING TEST. Killed by mutation M3.

    Asserted on the array the PROVIDER received, not on the registry's verdict —
    the gate could be perfectly correct and simply never consulted.
    """
    up_sink: list = []
    capabilities.register("browser", _UpBrowser())
    await _run(up_sink, session_key="lane-up")

    down_sink: list = []
    capabilities.clear()
    presented_tools.clear()
    capabilities.register("browser", _DownBrowser())
    await _run(down_sink, session_key="lane-down")

    up, down = set(up_sink[0]), set(down_sink[0])
    removed = up - down
    assert removed, (
        "no tool was removed when the browser capability went down — the gate is "
        f"not wired. Presented {len(up)} tools both times."
    )
    assert all(n.startswith("browser") for n in removed), (
        f"gating removed non-browser tools: {sorted(n for n in removed if not n.startswith('browser'))}"
    )
    assert not any(n.startswith("browser") for n in down), (
        f"browser tools survived the gate: {sorted(n for n in down if n.startswith('browser'))}"
    )


@pytest.mark.asyncio
async def test_protected_tools_survive_every_capability_being_down():
    """Invariant I2 — the anti-empty-toolbox guarantee.

    A probe bug must never be able to leave an owl with nothing. Protected tools
    are presented even when their capability reports unavailable.
    """
    from stackowl.tools._infra.presentation import _DEFAULT_ALWAYS, _DEFAULT_BASE

    sink: list = []
    capabilities.register("browser", _DownBrowser())
    await _run(sink, session_key="lane-protected")

    presented = set(sink[0])
    protected = _DEFAULT_BASE | _DEFAULT_ALWAYS
    # Only those that are actually registered in the default catalog.
    from stackowl.tools.registry import ToolRegistry
    registered = {t.name for t in ToolRegistry.with_defaults().all()}
    expected = protected & registered
    missing = expected - presented
    assert not missing, f"protected tools were gated away: {sorted(missing)}"


@pytest.mark.asyncio
async def test_the_gate_saves_real_tokens():
    """The gap was opened with a measurement; close it with the same one."""
    import json

    up_sink: list = []
    capabilities.register("browser", _UpBrowser())
    await _run(up_sink, session_key="lane-a")

    down_sink: list = []
    capabilities.clear()
    presented_tools.clear()
    capabilities.register("browser", _DownBrowser())
    await _run(down_sink, session_key="lane-b")

    from stackowl.tools.registry import ToolRegistry
    by_name = {t.name: t for t in ToolRegistry.with_defaults().all()}

    def cost(names):
        return sum(
            len(json.dumps({
                "name": n, "description": by_name[n].description,
                "input_schema": by_name[n].parameters,
            })) // 4
            for n in names if n in by_name
        )

    saved = cost(up_sink[0]) - cost(down_sink[0])
    assert saved > 500, f"expected a meaningful token saving, got {saved}"


@pytest.mark.asyncio
async def test_a_capability_coming_back_reaches_the_next_turn():
    """Invariant I5 — the D05.2 memo must not swallow the change.

    Without the capability→memo invalidation this fails: the gate is evaluated
    once per session, so a capability that recovers stays hidden until rollover.
    That is the "I added the API key and nothing happened" failure.
    """
    sink: list = []
    down = _DownBrowser()
    capabilities.register("browser", down)

    await _run(sink, session_key="one-lane")
    assert not any(n.startswith("browser") for n in sink[0])

    # The subsystem recovers and recycles.
    capabilities.register("browser", _UpBrowser())

    await _run(sink, session_key="one-lane")
    assert any(n.startswith("browser") for n in sink[1]), (
        "the capability recovered but the next turn still hid its tools — the "
        "memo was not invalidated"
    )


@pytest.mark.asyncio
async def test_tool_search_still_lists_a_gated_tool_with_reason_and_remedy():
    """Invariant I3 — nothing becomes unreachable.

    A gated tool is absent from the schema but MUST stay discoverable, annotated
    with why and how to fix it. This is the whole justification for not adopting
    the reference platform's "absent entirely": an owl that cannot see a
    capability can never report what is blocking it, let alone ask for it to be
    enabled.
    """
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.tools.registry import ToolRegistry

    capabilities.register("browser", _DownBrowser())
    reg = ToolRegistry.with_defaults()
    search = reg.get("tool_search")
    # tool_search resolves the registry from SERVICES, not from a kwarg — passing
    # registry= is silently ignored and yields "(no tools available)".
    token = set_services(StepServices(tool_registry=reg))
    try:
        result = await search.execute(query="browser navigate page")
    finally:
        reset_services(token)

    out = result.output or ""
    assert "browser" in out, f"a gated tool vanished from tool_search: {out[:200]}"
    assert "UNAVAILABLE" in out, f"no availability annotation: {out[:300]}"
    assert "missing libx11-xcb" in out, f"the reason was not surfaced: {out[:300]}"
    assert "sudo apt install libx11-xcb1" in out, f"the remedy was not surfaced: {out[:300]}"
