"""W3.T14 — deterministic capability substitution at the dispatch seam.

Two layers under test:

1. ``find_substitute`` (pure-ish) — given a failed tool + args, pick the
   highest-priority in-bounds NON-consequential sibling sharing the failed
   tool's ``capability_tag`` whose args can be built; or ``None``.

2. The dispatch seam (``execute._run_with_tools._dispatch``) — on a TOOL_FAILED
   result it deterministically runs that sibling through the SAME guarded path
   and feeds the sibling's SUCCESS output back as a fresh observation (prefixed
   with a neutral localized note). It is CONSENT-SAFE (never auto-runs a
   consequential sibling), BOUNDS-SAFE (skips out-of-bounds siblings), and
   capped at ONE substitution per capability_tag per turn.
"""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.capability_substitution import find_substitute
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# ---------------------------------------------------------------------------
# Fakes — minimal tools mirroring the web_knowledge capability class.
# browse  = consequential (the "broken" primary)
# search  = read sibling (the safe route-around)
# fetch   = read sibling (also servable from a url)
# ---------------------------------------------------------------------------

class _FakeTool(Tool):
    def __init__(
        self,
        name: str,
        *,
        severity: str = "read",
        capability_tag: str | None = None,
        output: str = "OK",
        succeed: bool = True,
        params: dict[str, object] | None = None,
    ) -> None:
        self._name = name
        self._severity = severity
        self._tag = capability_tag
        self._output = output
        self._succeed = succeed
        self._params = params or {}
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"fake {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return self._params

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self._params,
            action_severity=self._severity,  # type: ignore[arg-type]
            capability_tag=self._tag,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._succeed:
            return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)
        return ToolResult(success=False, output="", error="boom", duration_ms=1.0)


class _FakeRegistry:
    """Tiny stand-in for ToolRegistry exposing get()/all()."""

    def __init__(self, tools: list[_FakeTool]) -> None:
        self._by_name = {t.name: t for t in tools}

    def get(self, name: str) -> _FakeTool | None:
        return self._by_name.get(name)

    def all(self) -> list[_FakeTool]:
        return list(self._by_name.values())

    def to_provider_schema(self, protocol: str, **_kw: object) -> list[dict[str, object]]:
        # The provider is faked (it never reads the schema), so an empty list is fine.
        return []


def _web_tools(*, search_succeeds: bool = True) -> _FakeRegistry:
    """browser_browse (consequential) + web_search (read) + web_fetch (read).

    Uses the REAL adapter names so the T13 build_args_for adapters apply.
    """
    return _FakeRegistry([
        _FakeTool(
            "browser_browse", severity="consequential", capability_tag="web_knowledge",
            output="BROWSE_OK", succeed=False,
        ),
        _FakeTool(
            "web_search", severity="read", capability_tag="web_knowledge",
            output="SEARCH_OK", succeed=search_succeeds,
        ),
        _FakeTool(
            "web_fetch", severity="read", capability_tag="web_knowledge",
            output="FETCH_OK",
        ),
    ])


# ===========================================================================
# find_substitute — pure-function unit tests
# ===========================================================================

def test_find_substitute_picks_in_bounds_read_sibling():
    reg = _web_tools()
    # browser_browse failed with a query → web_search can serve it (query-based).
    result = find_substitute(
        "browser_browse", {"task": "weather today"},
        registry=reg, in_bounds=lambda _n: True, already_substituted=set(),
    )
    assert result is not None
    sib, built = result
    # web_search is the read sibling that can be built from a query.
    assert sib == "web_search"
    assert built == {"query": "weather today"}


def test_find_substitute_never_returns_consequential_sibling():
    """CONSENT-SAFETY: a consequential sibling is NEVER selected."""
    reg = _FakeRegistry([
        _FakeTool("browser_browse", severity="consequential", capability_tag="web_knowledge", succeed=False),
        # The ONLY other tagged sibling is ALSO consequential → must be excluded.
        _FakeTool("browser_browse_alt", severity="consequential", capability_tag="web_knowledge"),
    ])
    result = find_substitute(
        "browser_browse", {"task": "x"},
        registry=reg, in_bounds=lambda _n: True, already_substituted=set(),
    )
    assert result is None


def test_find_substitute_skips_out_of_bounds_sibling():
    reg = _web_tools()
    # web_search is out of bounds; web_fetch is in bounds but needs a url (none here).
    result = find_substitute(
        "browser_browse", {"task": "query only, no url"},
        registry=reg, in_bounds=lambda n: n != "web_search", already_substituted=set(),
    )
    # web_search excluded by bounds; web_fetch unservable (no url) → None.
    assert result is None


def test_find_substitute_skips_already_substituted_tag():
    reg = _web_tools()
    result = find_substitute(
        "browser_browse", {"task": "weather"},
        registry=reg, in_bounds=lambda _n: True,
        already_substituted={"web_knowledge"},
    )
    assert result is None


def test_find_substitute_none_when_no_servable_sibling():
    reg = _web_tools()
    # No query and no url → neither read sibling can be served.
    result = find_substitute(
        "browser_browse", {},
        registry=reg, in_bounds=lambda _n: True, already_substituted=set(),
    )
    assert result is None


def test_find_substitute_none_when_failed_tool_has_no_tag():
    reg = _FakeRegistry([
        _FakeTool("lonely", severity="read", capability_tag=None, succeed=False),
        _FakeTool("web_search", severity="read", capability_tag="web_knowledge"),
    ])
    result = find_substitute(
        "lonely", {"task": "x"},
        registry=reg, in_bounds=lambda _n: True, already_substituted=set(),
    )
    assert result is None


# ===========================================================================
# Dispatch-seam integration — drive _dispatch directly with a fake registry.
# ===========================================================================

@pytest.mark.asyncio
async def test_dispatch_routes_around_failed_tool(monkeypatch):
    """Route-around: failed browse → read sibling runs → sibling output returned."""
    from stackowl.pipeline.steps import execute as exe

    reg = _web_tools()
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("browser_browse", {"task": "weather today"})

    # The sibling actually ran with adapter-built args.
    search = reg.get("web_search")
    assert search is not None and search.calls == [{"query": "weather today"}]
    # The returned observation is the SIBLING's success output, NOT the marker.
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    assert not out.startswith(TOOL_FAILED_MARKER)
    assert "SEARCH_OK" in out


@pytest.mark.asyncio
async def test_dispatch_never_auto_runs_consequential_sibling(monkeypatch):
    """CONSENT-SAFE: only-consequential sibling → falls through to TOOL_FAILED."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    cons_sibling = _FakeTool(
        "browser_browse_alt", severity="consequential", capability_tag="web_knowledge",
        output="SHOULD_NOT_RUN",
    )
    reg = _FakeRegistry([
        _FakeTool("browser_browse", severity="consequential", capability_tag="web_knowledge", succeed=False),
        cons_sibling,
    ])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("browser_browse", {"task": "x"})

    # The consequential sibling did NOT run (no consent bypass).
    assert cons_sibling.calls == []
    assert out.startswith(TOOL_FAILED_MARKER)


@pytest.mark.asyncio
async def test_dispatch_skips_out_of_bounds_sibling(monkeypatch):
    """BOUNDS-SAFE: an out-of-bounds sibling is not executed."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    reg = _web_tools()
    # web_search is out of bounds; the failed args carry only a query (web_fetch
    # unservable). So no in-bounds servable sibling → TOOL_FAILED.
    dispatch = await _build_real_dispatch(
        monkeypatch, exe, reg, denied_tools={"web_search"},
    )

    out = await dispatch("browser_browse", {"task": "query only"})

    search = reg.get("web_search")
    assert search is not None and search.calls == []  # never ran
    assert out.startswith(TOOL_FAILED_MARKER)


@pytest.mark.asyncio
async def test_dispatch_one_substitution_per_capability_per_turn(monkeypatch):
    """A 2nd failure of the same capability in the same turn does NOT substitute."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    reg = _web_tools()
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    # First failure → substitutes (web_search runs once).
    out1 = await dispatch("browser_browse", {"task": "first"})
    assert "SEARCH_OK" in out1
    # Second failure of the SAME capability_tag → no second substitution.
    out2 = await dispatch("browser_browse", {"task": "second"})
    assert out2.startswith(TOOL_FAILED_MARKER)

    search = reg.get("web_search")
    assert search is not None and search.calls == [{"query": "first"}]  # only once


@pytest.mark.asyncio
async def test_dispatch_no_sibling_falls_through(monkeypatch):
    """No tagged sibling / unservable → honest TOOL_FAILED, no crash."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    reg = _FakeRegistry([
        _FakeTool("browser_browse", severity="consequential", capability_tag="web_knowledge", succeed=False),
        # the only sibling is unservable (no query/url available below)
    ])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("browser_browse", {})  # no query, no url
    assert out.startswith(TOOL_FAILED_MARKER)


# ===========================================================================
# F-6 — the substitution actuator loops over RANKED candidates: it marks only
# the TRIED sibling exhausted (NOT the whole capability tag) and advances to the
# next ranked sibling until one yields a trustworthy success or all are tried.
# ===========================================================================

def _two_read_siblings(*, search_succeeds: bool, fetch_succeeds: bool = True) -> _FakeRegistry:
    """browser_browse (broken primary) + TWO read siblings BOTH servable when the
    failed call carries a url AND a query: web_search (query) + web_fetch (url).
    Registry order makes web_search the first-ranked candidate, web_fetch second."""
    return _FakeRegistry([
        _FakeTool(
            "browser_browse", severity="consequential",
            capability_tag="web_knowledge", succeed=False,
        ),
        _FakeTool(
            "web_search", severity="read", capability_tag="web_knowledge",
            output="SEARCH_OK", succeed=search_succeeds,
        ),
        _FakeTool(
            "web_fetch", severity="read", capability_tag="web_knowledge",
            output="FETCH_OK", succeed=fetch_succeeds,
        ),
    ])


@pytest.mark.asyncio
async def test_dispatch_advances_to_next_sibling_when_first_fails(monkeypatch):
    """First ranked sibling FAILS → loop tries the SECOND, which succeeds. The
    capability tag is NOT excluded on the first sibling's failure (the bug)."""
    from stackowl.pipeline.steps import execute as exe

    reg = _two_read_siblings(search_succeeds=False, fetch_succeeds=True)
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("browser_browse", {"seed_url": "http://x", "task": "weather"})

    # web_search (rank 1) ran and failed; the loop advanced to web_fetch (rank 2).
    assert reg.get("web_search").calls == [{"query": "weather"}]
    assert reg.get("web_fetch").calls == [{"url": "http://x"}]
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    assert not out.startswith(TOOL_FAILED_MARKER)
    assert "FETCH_OK" in out


@pytest.mark.asyncio
async def test_dispatch_exhausts_all_siblings_then_falls_through(monkeypatch):
    """ALL ranked siblings fail → each is tried EXACTLY once, then honest surrender."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    reg = _two_read_siblings(search_succeeds=False, fetch_succeeds=False)
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("browser_browse", {"seed_url": "http://x", "task": "weather"})

    # Both siblings tried once (no sibling skipped, none retried).
    assert reg.get("web_search").calls == [{"query": "weather"}]
    assert reg.get("web_fetch").calls == [{"url": "http://x"}]
    assert out.startswith(TOOL_FAILED_MARKER)


# ===========================================================================
# F-7 — RUNG 1 retry-once now also covers a TRANSIENT genuine failure
# (success=False whose error looks like an infrastructure fault), not only an
# unverified effect. Bounded to once per tool; consequential tools never retried.
# ===========================================================================

class _FlakyTool(_FakeTool):
    """A tool that fails its first ``fail_times`` calls with ``error`` then
    succeeds. Models a transient (or deterministic, when fail_times is large)
    genuine failure."""

    def __init__(
        self,
        name: str,
        *,
        severity: str = "write",
        error: str = "Connection refused",
        fail_times: int = 1,
        output: str = "WROTE_OK",
        capability_tag: str | None = None,
    ) -> None:
        super().__init__(name, severity=severity, capability_tag=capability_tag, output=output)
        self._error = error
        self._fail_times = fail_times
        self._n = 0

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        self._n += 1
        if self._n <= self._fail_times:
            return ToolResult(success=False, output="", error=self._error, duration_ms=1.0)
        return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)


@pytest.mark.asyncio
async def test_dispatch_retries_transient_genuine_failure(monkeypatch):
    """A transient (Connection refused) genuine failure is retried ONCE; the retry
    succeeds and its output is returned."""
    from stackowl.pipeline.steps import execute as exe

    flaky = _FlakyTool("flaky_writer", error="Connection refused by host", fail_times=1)
    reg = _FakeRegistry([flaky])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("flaky_writer", {"path": "/tmp/x"})

    assert len(flaky.calls) == 2  # initial + one retry
    assert "WROTE_OK" in out


@pytest.mark.asyncio
async def test_dispatch_does_not_retry_nontransient_failure(monkeypatch):
    """A deterministic (non-transient) genuine failure gets ZERO retries."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    flaky = _FlakyTool("steady_fail", error="invalid argument: bad path", fail_times=99)
    reg = _FakeRegistry([flaky])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("steady_fail", {"path": "x"})

    assert len(flaky.calls) == 1  # not retried
    assert out.startswith(TOOL_FAILED_MARKER)


@pytest.mark.asyncio
async def test_dispatch_transient_retry_bounded_to_once(monkeypatch):
    """A transient failure that NEVER heals is retried exactly once, then floored."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    flaky = _FlakyTool("flaky2", error="Connection reset", fail_times=99)
    reg = _FakeRegistry([flaky])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("flaky2", {"path": "x"})

    assert len(flaky.calls) == 2  # initial + exactly one retry
    assert out.startswith(TOOL_FAILED_MARKER)


@pytest.mark.asyncio
async def test_dispatch_never_retries_consequential_transient_failure(monkeypatch):
    """CONSEQUENTIAL tools are NEVER auto-retried even on a transient failure."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
    from stackowl.pipeline.steps import execute as exe

    flaky = _FlakyTool("danger", severity="consequential", error="Connection reset", fail_times=99)
    reg = _FakeRegistry([flaky])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("danger", {"path": "x"})

    assert len(flaky.calls) == 1  # consequential never re-fired blind
    assert out.startswith(TOOL_FAILED_MARKER)


# ===========================================================================
# F-5 — the substitution actuator distinguishes "no sibling exists" (surrender)
# from "the actuator MACHINERY broke running one sibling". A per-sibling machinery
# fault (e.g. a ledger-guard error — NOT the tool merely returning failure, which
# Tool.__call__ already wraps into a ToolResult) must advance to the NEXT ranked
# candidate rather than the single outer `except → return None` collapsing the
# whole capability class on one broken sibling.
# ===========================================================================

@pytest.mark.asyncio
async def test_substitute_actuator_error_advances_to_next_candidate(monkeypatch):
    """F-5 — the ACTUATOR explodes running the first-ranked sibling; the loop must
    try the SECOND candidate before surrendering."""
    import importlib
    lg_mod = importlib.import_module("stackowl.pipeline.durable.ledger_guard")
    from stackowl.pipeline.steps import execute as exe

    reg = _two_read_siblings(search_succeeds=True, fetch_succeeds=True)

    # Every sibling is in bounds.
    monkeypatch.setattr(
        "stackowl.authz.bounds_guard.check_effective_bounds",
        lambda effective, name: None,  # noqa: ARG005
    )

    # The actuator MACHINERY faults for the first-ranked sibling (web_search) but
    # works for the second (web_fetch). This models a per-sibling break distinct
    # from a tool that merely returns success=False.
    async def flaky_guard(name, args, severity, execute_fn):  # noqa: ANN001
        if name == "web_search":
            raise RuntimeError("ledger actuator exploded")
        return await execute_fn()

    monkeypatch.setattr(lg_mod, "ledger_guard", flaky_guard)

    out = await exe._try_substitute(
        failed_tool="browser_browse",
        failed_args={"seed_url": "http://x", "task": "weather"},
        tool_registry=reg,  # type: ignore[arg-type]
        effective=None,
        substituted_tags=set(),
        trace_id="t1",
        locale="en",
    )

    # Before the fix the outer `except` caught web_search's actuator error and
    # returned None (surrender). After: advance to web_fetch and return its output.
    assert out is not None
    assert "FETCH_OK" in out
    assert reg.get("web_fetch").calls == [{"url": "http://x"}]


@pytest.mark.asyncio
async def test_substitute_actuator_error_on_all_siblings_surrenders(monkeypatch):
    """F-5 — when EVERY sibling's actuator faults, surrender honestly (None), and
    each candidate is attempted exactly once (no spiral)."""
    import importlib
    lg_mod = importlib.import_module("stackowl.pipeline.durable.ledger_guard")
    from stackowl.pipeline.steps import execute as exe

    reg = _two_read_siblings(search_succeeds=True, fetch_succeeds=True)
    monkeypatch.setattr(
        "stackowl.authz.bounds_guard.check_effective_bounds",
        lambda effective, name: None,  # noqa: ARG005
    )

    seen: list[str] = []

    async def always_faults(name, args, severity, execute_fn):  # noqa: ANN001
        seen.append(name)
        raise RuntimeError("actuator exploded")

    monkeypatch.setattr(lg_mod, "ledger_guard", always_faults)

    out = await exe._try_substitute(
        failed_tool="browser_browse",
        failed_args={"seed_url": "http://x", "task": "weather"},
        tool_registry=reg,  # type: ignore[arg-type]
        effective=None,
        substituted_tags=set(),
        trace_id="t1",
        locale="en",
    )

    assert out is None
    # Both ranked siblings tried exactly once — no candidate retried, no spiral.
    assert seen == ["web_search", "web_fetch"]


# ---------------------------------------------------------------------------
# Helper: build the REAL _dispatch closure with a stubbed services/bounds seam.
# ---------------------------------------------------------------------------

async def _build_real_dispatch(
    monkeypatch,
    exe,
    reg: _FakeRegistry,
    *,
    denied_tools: set[str] | None = None,
    language: str = "en",
):
    """Construct execute._run_with_tools' inner _dispatch with the substitution
    wiring active, stubbing the bounds + consent + services seams so the test
    isolates the substitution behavior.

    We capture _dispatch by running _run_with_tools with a fake provider whose
    complete_with_tools simply hands the dispatcher back to us.

    The consent gate APPROVES every tool here so the consequential PRIMARY
    actually RUNS and FAILS (producing the TOOL_FAILED marker that triggers
    substitution) — modeling a user-approved consequential action that then
    failed mid-loop. CONSENT-SAFETY of the substitution is enforced INSIDE
    find_substitute (severity filter), asserted separately.
    """
    from stackowl.pipeline.state import PipelineState

    denied = denied_tools or set()

    # Stub bounds: in-bounds unless in `denied`. _dispatch imports these names
    # locally from their source modules, so patching the source attrs takes.
    def _fake_check_effective_bounds(effective, tool_name):  # noqa: ANN001
        return None if tool_name not in denied else f"'{tool_name}' is out of bounds"

    def _fake_compute_effective_bounds(state, owl_registry):  # noqa: ANN001
        return None  # unbounded sentinel; the stubbed check decides

    monkeypatch.setattr(
        "stackowl.authz.bounds_guard.check_effective_bounds",
        _fake_check_effective_bounds,
    )
    monkeypatch.setattr(
        "stackowl.pipeline.authz_compose.compute_effective_bounds",
        _fake_compute_effective_bounds,
    )

    class _Gate:
        async def check(self, tool, *, channel=None, session_id=None, call_args=None):  # noqa: ANN001
            return True  # approve all → the primary runs and fails

    class _Services:
        """All-None services except the consent gate; any unset attr → None so
        _run_with_tools' many service reads (turn_registry, cost_tracker, …) are
        harmlessly inert for this isolated dispatch test."""

        consent_gate = _Gate()

        def __getattr__(self, _name: str) -> None:  # noqa: D105
            return None

    monkeypatch.setattr(exe, "get_services", lambda: _Services())

    captured: dict[str, Any] = {}

    class _FakeProvider:
        protocol = "anthropic"

        async def complete_with_tools(self, *, tool_dispatcher, **kw):  # noqa: ANN001
            captured["dispatch"] = tool_dispatcher
            return ("", [])

    state = PipelineState(
        input_text="hi", owl_name="secretary", session_id="s1",
        channel="cli", trace_id="t1", pipeline_step="execute",
        language=language,
    )

    await exe._run_with_tools(state, _FakeProvider(), reg)  # type: ignore[arg-type]
    return captured["dispatch"]
