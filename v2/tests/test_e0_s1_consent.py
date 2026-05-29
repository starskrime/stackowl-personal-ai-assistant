"""E0-S1 — consent gate + combination consent policy.

Covers the operator-voted combination UX (trust tiers + session batch +
time-window + always-ask exclusions), fail-closed defaults, and audit.

See _bmad-output/planning-artifacts/stories/E0-tool-safety-foundation/
E0-S1-wire-consent-gate.md and readiness-check.md Section 9 (decision #1, #7).
"""

from __future__ import annotations

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import (
    ConsentPolicy,
    ConsentRequest,
    ConsentScope,
    TrustTier,
)
from stackowl.tools.registry import ConsequentialActionGate

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeClock:
    """Monotonic clock whose value the test advances explicitly."""

    def __init__(self) -> None:
        self._t = 1000.0

    def monotonic(self) -> float:
        return self._t

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover
        self._t += seconds

    def advance(self, seconds: float) -> None:
        self._t += seconds


class _RecordingPrompter:
    """Prompter that returns a scripted scope and records every request."""

    def __init__(self, scope: ConsentScope) -> None:
        self._scope = scope
        self.requests: list[ConsentRequest] = []

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.requests.append(req)
        return self._scope


class _RaisingPrompter:
    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        raise RuntimeError("transport down")


class _FakeAudit:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append(self, event_type: str, actor: str, target: str | None, details: dict[str, object]) -> None:
        self.rows.append({"event_type": event_type, "actor": actor, "target": target, "details": details})


class _StubConsequentialTool(Tool):
    def __init__(self, name: str = "danger") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "A consequential stub."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ran", duration_ms=1.0)


class _StubReadTool(_StubConsequentialTool):
    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(name=self._name, description=self.description, parameters=self.parameters)


# ---------------------------------------------------------------------------
# ConsentPolicy — trust tiers
# ---------------------------------------------------------------------------


async def test_tier_auto_allows_without_prompting() -> None:
    prompter = _RecordingPrompter(ConsentScope.DENY)
    policy = ConsentPolicy(prompter=prompter, tiers={"t": TrustTier.AUTO})
    allowed = await policy.request(tool_name="t", channel="cli", session_id="s1")
    assert allowed is True
    assert prompter.requests == []  # never prompted


async def test_tier_never_denies_without_prompting() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter, tiers={"t": TrustTier.NEVER})
    allowed = await policy.request(tool_name="t", channel="cli", session_id="s1")
    assert allowed is False
    assert prompter.requests == []


async def test_default_tier_prompts_and_user_once_allows() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter)
    allowed = await policy.request(tool_name="t", channel="cli", session_id="s1")
    assert allowed is True
    assert len(prompter.requests) == 1


async def test_user_deny_blocks() -> None:
    prompter = _RecordingPrompter(ConsentScope.DENY)
    policy = ConsentPolicy(prompter=prompter)
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is False


# ---------------------------------------------------------------------------
# ConsentPolicy — session batch & time-window grants
# ---------------------------------------------------------------------------


async def test_once_grant_does_not_suppress_reprompt() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter)
    await policy.request(tool_name="t", channel="cli", session_id="s1")
    await policy.request(tool_name="t", channel="cli", session_id="s1")
    assert len(prompter.requests) == 2  # ONCE re-prompts every time


async def test_session_batch_suppresses_reprompt_same_session_only() -> None:
    prompter = _RecordingPrompter(ConsentScope.SESSION)
    policy = ConsentPolicy(prompter=prompter)
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is True
    # second call in same session: no prompt
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is True
    assert len(prompter.requests) == 1
    # different session must re-prompt
    assert await policy.request(tool_name="t", channel="cli", session_id="s2") is True
    assert len(prompter.requests) == 2


async def test_time_window_grant_expires() -> None:
    clock = _FakeClock()
    prompter = _RecordingPrompter(ConsentScope.WINDOW)
    policy = ConsentPolicy(prompter=prompter, clock=clock, window_seconds=900.0)
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is True
    # within window: no re-prompt
    clock.advance(300.0)
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is True
    assert len(prompter.requests) == 1
    # past window: re-prompts
    clock.advance(601.0)
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is True
    assert len(prompter.requests) == 2


async def test_ask_once_session_tier_auto_after_first_approval() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter, tiers={"t": TrustTier.ASK_ONCE_SESSION})
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is True
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is True
    assert len(prompter.requests) == 1  # asked once, then auto for the session


# ---------------------------------------------------------------------------
# ConsentPolicy — always-ask exclusions (execute_code / computer_use / ha locks)
# ---------------------------------------------------------------------------


async def test_excluded_tool_reprompts_despite_session_grant() -> None:
    prompter = _RecordingPrompter(ConsentScope.SESSION)
    policy = ConsentPolicy(prompter=prompter, always_ask_tools=frozenset({"execute_code"}))
    assert await policy.request(tool_name="execute_code", channel="cli", session_id="s1") is True
    assert await policy.request(tool_name="execute_code", channel="cli", session_id="s1") is True
    assert len(prompter.requests) == 2  # always re-prompts, batch never recorded


async def test_excluded_category_reprompts_despite_window() -> None:
    clock = _FakeClock()
    prompter = _RecordingPrompter(ConsentScope.WINDOW)
    policy = ConsentPolicy(
        prompter=prompter, clock=clock, always_ask_categories=frozenset({"lock"})
    )
    assert await policy.request(tool_name="ha", channel="cli", session_id="s1", category="lock") is True
    assert await policy.request(tool_name="ha", channel="cli", session_id="s1", category="lock") is True
    assert len(prompter.requests) == 2


async def test_excluded_tool_prompt_disallows_relaxation() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter, always_ask_tools=frozenset({"execute_code"}))
    await policy.request(tool_name="execute_code", channel="cli", session_id="s1")
    assert prompter.requests[0].allow_relaxation is False


async def test_nonexcluded_tool_prompt_allows_relaxation() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter)
    await policy.request(tool_name="t", channel="cli", session_id="s1")
    assert prompter.requests[0].allow_relaxation is True


# ---------------------------------------------------------------------------
# ConsentPolicy — fail-closed
# ---------------------------------------------------------------------------


async def test_default_policy_fails_closed() -> None:
    """No prompter wired → deny."""
    policy = ConsentPolicy()
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is False


async def test_prompter_exception_fails_closed() -> None:
    policy = ConsentPolicy(prompter=_RaisingPrompter())
    assert await policy.request(tool_name="t", channel="cli", session_id="s1") is False


async def test_unknown_channel_in_routing_fails_closed() -> None:
    from stackowl.tools.consent import RoutingPrompter

    routing = RoutingPrompter()  # nothing registered
    policy = ConsentPolicy(prompter=routing)
    assert await policy.request(tool_name="t", channel="telegram", session_id="s1") is False


# ---------------------------------------------------------------------------
# ConsentPolicy — audit
# ---------------------------------------------------------------------------


async def test_every_decision_is_audited() -> None:
    audit = _FakeAudit()
    prompter = _RecordingPrompter(ConsentScope.DENY)
    policy = ConsentPolicy(prompter=prompter, audit_logger=audit)
    await policy.request(tool_name="t", channel="cli", session_id="s1")
    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["event_type"] == "consent.decision"
    assert row["target"] == "t"
    assert row["details"]["decision"] == "deny"


async def test_auto_allow_is_audited() -> None:
    audit = _FakeAudit()
    policy = ConsentPolicy(prompter=_RecordingPrompter(ConsentScope.DENY), audit_logger=audit, tiers={"t": TrustTier.AUTO})
    await policy.request(tool_name="t", channel="cli", session_id="s1")
    assert audit.rows[0]["details"]["decision"] == "allow"


# ---------------------------------------------------------------------------
# ConsequentialActionGate — wiring to policy
# ---------------------------------------------------------------------------


async def test_gate_allows_read_tool_without_policy_call() -> None:
    prompter = _RecordingPrompter(ConsentScope.DENY)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    assert await gate.check(_StubReadTool()) is True
    assert prompter.requests == []


async def test_gate_delegates_consequential_to_policy() -> None:
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    assert await gate.check(_StubConsequentialTool(), channel="cli", session_id="s1") is True
    assert len(prompter.requests) == 1


async def test_gate_blocks_when_policy_denies() -> None:
    gate = ConsequentialActionGate(ConsentPolicy(prompter=_RecordingPrompter(ConsentScope.DENY)))
    assert await gate.check(_StubConsequentialTool(), channel="cli", session_id="s1") is False


async def test_gate_default_construction_fails_closed() -> None:
    gate = ConsequentialActionGate()
    assert await gate.check(_StubConsequentialTool(), channel="telegram", session_id="s1") is False


async def test_gate_backward_compat_sync_confirm_fn() -> None:
    """Legacy (name)->bool confirm_fn still works behind the async API."""
    calls: list[str] = []

    def _confirm(name: str) -> bool:
        calls.append(name)
        return True

    gate = ConsequentialActionGate(confirm_fn=_confirm)
    assert await gate.check(_StubConsequentialTool(name="x"), channel="cli", session_id="s1") is True
    assert calls == ["x"]


async def test_never_tier_blocks_excluded_tool_without_prompt() -> None:
    """NEVER precedence: hard block even for an always-ask excluded tool."""
    prompter = _RecordingPrompter(ConsentScope.ONCE)
    policy = ConsentPolicy(
        prompter=prompter, tiers={"execute_code": TrustTier.NEVER},
        always_ask_tools=frozenset({"execute_code"}),
    )
    assert await policy.request(tool_name="execute_code", channel="cli", session_id="s1") is False
    assert prompter.requests == []


async def test_auto_tier_still_prompts_excluded_tool() -> None:
    """AUTO must NOT auto-allow an excluded tool — it still prompts."""
    prompter = _RecordingPrompter(ConsentScope.DENY)
    policy = ConsentPolicy(
        prompter=prompter, tiers={"execute_code": TrustTier.AUTO},
        always_ask_tools=frozenset({"execute_code"}),
    )
    assert await policy.request(tool_name="execute_code", channel="cli", session_id="s1") is False
    assert len(prompter.requests) == 1  # prompted despite AUTO


async def test_empty_session_does_not_record_standing_grant() -> None:
    """An empty session_id must not collapse all callers into one batch bucket."""
    prompter = _RecordingPrompter(ConsentScope.SESSION)
    policy = ConsentPolicy(prompter=prompter)
    await policy.request(tool_name="t", channel="cli", session_id="")
    await policy.request(tool_name="t", channel="cli", session_id="")
    assert len(prompter.requests) == 2  # no standing grant recorded for empty session


async def test_gate_derives_category_from_manifest() -> None:
    """B2 fix — the always-ask category comes from the manifest, not LLM args."""

    class _LockTool(_StubConsequentialTool):
        @property
        def manifest(self) -> ToolManifest:
            return ToolManifest(
                name=self._name, description=self.description, parameters=self.parameters,
                action_severity="consequential", consent_category="lock",
            )

    prompter = _RecordingPrompter(ConsentScope.SESSION)
    gate = ConsequentialActionGate(
        ConsentPolicy(prompter=prompter, always_ask_categories=frozenset({"lock"}))
    )
    tool = _LockTool(name="ha")
    assert await gate.check(tool, channel="cli", session_id="s1") is True
    assert await gate.check(tool, channel="cli", session_id="s1") is True
    assert len(prompter.requests) == 2  # lock category always re-prompts


@pytest.mark.parametrize("severity", ["read", "write"])
async def test_gate_skips_nonconsequential(severity: str) -> None:
    prompter = _RecordingPrompter(ConsentScope.DENY)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))

    class _T(_StubConsequentialTool):
        @property
        def manifest(self) -> ToolManifest:
            return ToolManifest(
                name=self._name, description=self.description,
                parameters=self.parameters, action_severity=severity,  # type: ignore[arg-type]
            )

    assert await gate.check(_T()) is True
    assert prompter.requests == []
