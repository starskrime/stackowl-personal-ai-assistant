"""Self-Healing Turn Supervisor — the LYING-JUDGE merge gate (W1.T6).

THE headline merge gate (Murat's gate): prove the zombie is DEAD.

A judge that is ALIVE and WRONG — it returns ``{"delivered": true}`` on a turn that
is structurally a give-up — no longer gets to silently accept the give-up. The
ALWAYS-ON structural veto (``supervisor.decide_nudge`` → ``apply_structural_veto``)
overrides the lying judge's DELIVERED verdict and nudges the loop, so the turn
delivers a REAL answer instead of the trivial refusal.

This is the W1-achievable slice: it uses a TRIVIAL give-up draft ("No." — <4 chars,
so ``_structurally_irrelevant`` is True), which is the structural veto's exact
domain. The structural signal fires only on the genuine zombie shape:
``tool_failures >= 1 AND successful_tool_calls == 0 AND draft is trivial`` — here a
tool FAILED (DNS-unreachable), nothing succeeded, and the draft is "No.".

(The polished/substantive give-up case — a fluent refusal that is NOT structurally
trivial — is NOT covered here. That is W3's job: substitution fires at the
tool-failure site, with W2's never-empty floor as the final backstop. This gate is
deliberately scoped to the structural-veto slice that W1 already makes LIVE.)

WHY a lying judge and not a not-delivered one: ``build_persistence_check`` is
FAIL-OPEN. Any "delivered" verdict (honest, hallucinated, or judge-error) makes the
checker return ``None`` — NO judge directive. So with the lying judge installed on
BOTH the primary (``get_with_cascade("fast")``) and fallback
(``get_with_cascade("local")``) tiers, the checker contributes NOTHING. If the turn
still does not accept the give-up, it can ONLY be the structural veto that caught it
— which is exactly what this gate proves end-to-end through the REAL wiring.

REAL (everything except the AI provider + the judge): the whole AsyncioBackend
pipeline (scanner → triage → execute → deliver), the REAL ``ToolRegistry`` +
``_dispatch`` (which prefixes a failed ``ToolResult`` with ``TOOL_FAILED_MARKER`` so
the provider records ``failed=True``), the REAL ``OpenAIProvider.complete_with_tools``
ReAct loop, and the REAL ``build_persistence_check`` → ``decide_nudge`` →
``apply_structural_veto`` veto path. FAKED: ONLY the main AI provider (a scripted
fake OpenAI SDK client) and the judge (a scripted ``{delivered:true}`` provider on
fast+local).

Mirrors the gateway harness in ``tests/pipeline/test_phaseD_persistence.py`` §3
(``_FakeClient`` driving a real ``OpenAIProvider``, the judge-routing provider
pattern, ``_drive_gateway``) — but flips the judge from not-delivered to the LYING
``{delivered:true}`` and uses a trivial give-up + a genuinely-failing tool so the
STRUCTURAL veto, not the judge, is the thing that catches the zombie.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- #
# The failing tool — a tiny browse-like capability whose execute() returns a
# FAILED ToolResult (simulating a DNS / unreachable-host failure). The REAL
# _dispatch prefixes the rendered error with TOOL_FAILED_MARKER, so the provider
# records failed=True for this call — that is what arms the structural veto.
# --------------------------------------------------------------------------- #

_BROWSE_TOOL = "browse_site"
_DNS_ERROR = "NS_ERROR_UNKNOWN_HOST"


class _UnreachableBrowseTool(Tool):
    """A browse-capability tool that always fails (host unreachable)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return _BROWSE_TOOL

    @property
    def description(self) -> str:
        return "Fetch a web page (fails: host unreachable in this environment)."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(
            success=False, output="", error=_DNS_ERROR, duration_ms=0.0
        )


# --------------------------------------------------------------------------- #
# The LYING judge — alive and WRONG. Serves BOTH triage routing (returns an owl
# name) AND the persistence judge (returns {delivered:true}). We disambiguate by
# the judge prompt's distinctive 'AGENT DRAFT REPLY' marker (persistence.py:312).
# Installed on BOTH fast (primary judge) and local (fallback judge) tiers so the
# WHOLE judge cascade rules "delivered" — proving the VETO, not the judge, is what
# catches the give-up.
# --------------------------------------------------------------------------- #


class _LyingJudgeProvider(ModelProvider):
    """{delivered:true} on every draft — the alive-and-wrong judge. Doubles as the
    triage router (returns 'secretary' for non-judge prompts)."""

    @property
    def name(self) -> str:
        return "lying-judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        # ALIVE AND WRONG on a judge prompt: rule the give-up {delivered:true};
        # any other prompt is triage routing → return the owl name.
        content = (
            '{"delivered": true, "reason": "looks complete"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="lying-judge-fake",
            provider_name="lying-judge-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


# --------------------------------------------------------------------------- #
# Fake OpenAI SDK client (shape from tests/providers/test_react_protocol.py /
# tests/pipeline/test_phaseD_persistence.py) driving a REAL OpenAIProvider.
# --------------------------------------------------------------------------- #


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._i = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        idx = min(self._i, len(self._responses) - 1)
        resp = self._responses[idx]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(responses))


def _make_main_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


# The non-empty, capability-naming real answer the model emits AFTER the veto's
# nudge. Names the blocked capability + what it tried (browse) and offers a real
# substitute — the opposite of the trivial "No." give-up.
_REAL_ANSWER = (
    "I couldn't reach that site — the browse capability failed (the host was "
    "unreachable). Here is what I can tell you from what I already know instead, "
    "and you can paste the page text if you'd like me to work from it directly."
)


def _build_services(
    provider: OpenAIProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    # The LYING judge serves triage routing (fast) AND BOTH judge tiers:
    #   primary  = get_with_cascade("fast")  -> resolves the fast-tier instance
    #   fallback = get_with_cascade("local") -> resolves the local-tier instance
    # Same lying instance on both so the ENTIRE judge cascade rules {delivered:true}.
    judge = _LyingJudgeProvider()
    preg.register_mock("router", judge, tier="fast")
    preg.register_mock("local-judge", judge, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


@pytest.mark.asyncio
async def test_lying_judge_giveup_is_vetoed_end_to_end(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A judge that lies {delivered:true} on a structural give-up is OVERRIDDEN by
    the always-on structural veto; the turn delivers iteration-2's real answer, not
    the trivial "No." give-up. Drives the REAL pipeline; mocks ONLY the AI + judge."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Iteration 0: call the browse tool — it FAILS (host unreachable) → recorded
    #              failed=True via the REAL _dispatch + TOOL_FAILED_MARKER.
    # Iteration 1: a TRIVIAL give-up draft ("No." — <4 chars, structurally a give-up),
    #              with NO successful tool call → arms the structural veto.
    # Iteration 2 (AFTER the veto-injected nudge): a real, non-empty answer naming
    #              the blocked capability + offering a substitute.
    browse = _FakeMessage(
        content=f'ACTION: {_BROWSE_TOOL}\n```json\n{{"url": "https://nope.invalid"}}\n```',
        tool_calls=None,
    )
    giveup = _FakeMessage(content="No.", tool_calls=None)
    real = _FakeMessage(content=_REAL_ANSWER, tool_calls=None)
    client = _FakeClient(
        [_FakeResponse(browse), _FakeResponse(giveup), _FakeResponse(real)]
    )
    provider = _make_main_provider(client)

    owl_registry = OwlRegistry.with_default_secretary()
    tool = _UnreachableBrowseTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    msg = IngressMessage(
        text="please summarize that page for me",
        session_id="sess-lying-judge",
        channel="cli",
        trace_id="trace-lying-judge-1",
    )
    decision = scanner.scan(msg)
    input_text = (
        decision.stripped_text if decision.stripped_text is not None else msg.text
    )
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )

    final_state = await backend.run(state)
    delivered = "".join(c.content for c in final_state.responses)

    # ===================================================================
    # OUTCOME 1 — the give-up was NOT the terminal answer: the loop made a
    # 3rd provider call, i.e. the veto fired AFTER the "No." and the model got
    # to produce iteration-2's real answer. (browse round + give-up round +
    # post-nudge real-answer round = 3.)
    # ===================================================================
    assert len(client.chat.completions.calls) == 3, (
        "MERGE-GATE FAIL: the loop did not make the expected 3 provider calls — "
        "the veto did not nudge after the trivial give-up, so the lying judge "
        f"silently accepted the give-up. Calls: {len(client.chat.completions.calls)}"
    )

    # ===================================================================
    # OUTCOME 2 — the FINAL user-visible response is iteration-2's REAL answer,
    # NON-EMPTY and NOT the trivial "No." give-up. The structural veto overrode
    # the lying judge and the turn did NOT accept the give-up.
    # ===================================================================
    assert delivered.strip(), (
        "MERGE-GATE FAIL: the delivered response is empty — the turn accepted "
        "the give-up."
    )
    assert delivered.strip() != "No.", (
        "MERGE-GATE FAIL: the delivered response IS the trivial give-up 'No.' — "
        "the lying judge's {delivered:true} was accepted; the veto did NOT fire."
    )
    assert _REAL_ANSWER in delivered, (
        "MERGE-GATE FAIL: the delivered response is not iteration-2's real answer. "
        f"Delivered: {delivered!r}"
    )

    # ===================================================================
    # OUTCOME 3 (acknowledgement) — the real answer NAMES the failed capability
    # (browse) + what it tried, proving it's a genuine substitute, not a refusal.
    # ===================================================================
    assert "browse" in delivered.lower(), (
        "MERGE-GATE FAIL: the recovery answer does not name the failed capability."
    )

    # Wiring sanity: the failing tool genuinely RAN (that is what armed the
    # structural signal: 1 failed, 0 succeeded).
    assert tool.calls, "the browse tool never ran — the failure signal was never armed"

    # The directive verifying the VETO (not the judge) fired: the persistence
    # directive was injected as a user turn before the 3rd (real-answer) call.
    from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE

    third_call = client.chat.completions.calls[2]
    assert any(
        m.get("role") == "user" and m.get("content") == PERSISTENCE_DIRECTIVE
        for m in third_call
    ), (
        "MERGE-GATE FAIL: the persistence directive was not injected before the "
        "recovery call — the veto did not drive the nudge through the real loop."
    )
