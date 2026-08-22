"""D01.1 — the frozen system prompt, proven on the REAL gateway path.

WHAT THIS PROVES, and why the unit tests were not enough.
``tests/pipeline/test_prompt_freeze.py`` drives ``assemble.run`` directly with an
in-memory store. That establishes the step's behaviour but not that the whole
chain is wired: gateway scan -> route -> PipelineState -> backend -> the pipeline
-> the REAL SessionPromptStore against a REAL database -> the provider actually
receiving one byte-identical system message. This file asserts the business
requirement as a user would state it:

    "Two messages in the same conversation must send the model the same
     system prompt, so the provider's automatic prefix cache can hit."

Only the AI provider is faked. The scanner, the router decision, the pipeline
steps, the owl registry, the prompt store and the database are all real.

WHY THE PERSONA IS MUTATED MID-CONVERSATION. Two ordinary turns would produce
identical prompts even if the freeze were entirely absent and the prompt were
rebuilt from scratch each time — nothing in a test differs between them. That
assertion would pass on broken code, which is precisely the failure this item
already suffered once: the intent-class equality assertion in
``test_assemble_skill_catalogue.py`` passed for a whole slice while DEBT-24 was
live, because its harness registered no tool_registry and so nothing could
differ. An assertion that cannot fail reads as coverage without being coverage.

So the owl's persona is CHANGED between turn 1 and turn 2. A frozen prompt keeps
the original; a rebuilt one picks up the change. The test can now fail, and it
encodes a decision Bakir actually made (Q14: persona is "frozen for the life of
a conversation", with evolution landing at the next rollover).

MUTATION-TESTED 2026-07-27, rather than assumed. Disabling the cache hit in
assemble (`if False and cached is not None`) turns
``test_two_turns_of_one_conversation_send_a_byte_identical_system_prompt`` RED.
Two honest notes from that run:

  * The byte-equality assertion ALONE does not bite — with the freeze disabled,
    both rebuilt prompts were still identical, because nothing in a test harness
    differs between two turns. It is the persona-mutation assertion that fails.
    The same run showed test_prompt_freeze.py's equality assertion surviving the
    mutation too; only its ``saved == 1`` count caught the break. Byte-equality
    is the requirement worth STATING, but it is not the assertion doing the work.
  * ``test_a_new_conversation_picks_up_the_evolved_persona`` also survives the
    mutation, and cannot be otherwise: a prompt rebuilt every turn trivially
    picks up the change. It is not a freeze test on its own. It earns its place
    as the other jaw of the vice — the first test pins the prompt FROZEN within a
    conversation, this one pins it RELEASED at the boundary, and a cache that
    was simply stuck would fail this one while passing that one.
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
from stackowl.sessions.prompt_store import SessionPromptStore
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

LANE = "owl:secretary:cli:dm:d011"
# The incarnation id. Real turns get this from sessions/ingress.py::
# resolve_turn_session; the gateway harness does not run ingress, so it is set
# explicitly here. It is NOT decoration: assemble gates both the cache read and
# the freeze write on a non-empty conversation_id, so a blank one would silently
# disable the very thing this file exists to test (see DEBT-27).
RUN = "20260727_090000_d011aaaa"
NEXT_RUN = "20260728_090000_d011bbbb"

_PERSONA_BEFORE = "PERSONA-MARKER-ORIGINAL-4f21"
_PERSONA_AFTER = "PERSONA-MARKER-MUTATED-9c07"

_TOOL_NAME = "d011_probe"
_TOOL_MARKER = "D011-TOOL-RAN-8bd3"


# --------------------------------------------------------------------------- #
# Fakes — the AI provider only.
# --------------------------------------------------------------------------- #


class _ProbeTool(Tool):
    """A deterministic read-severity tool, so dispatch permits it with no consent
    gate wired. Records that it ran."""

    name = _TOOL_NAME
    description = "probe tool for the D01.1 frozen-prompt integration test"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": [],
    }

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=True, output=_TOOL_MARKER)


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
        # Shallow-copied per invocation so the provider loop's later in-place
        # mutation cannot corrupt what we recorded.
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        resp = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(responses))


class _RoutingProvider(ModelProvider):
    """FAST tier. The triage step's router calls complete() here, so it does not
    consume the sequenced answers reserved for execute."""

    @property
    def name(self) -> str:
        return "routing-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="secretary", input_tokens=1, output_tokens=1,
            model="routing-fake", provider_name="routing-fake", duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


class _JudgeProvider(ModelProvider):
    """STANDARD/LOCAL tiers. The give-up judge rules DELIVERED here rather than
    cascading onto — and consuming a response from — the answer provider."""

    @property
    def name(self) -> str:
        return "judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content='{"delivered": true, "reason": "answer is complete"}',
            input_tokens=1, output_tokens=1,
            model="judge-fake", provider_name="judge-fake", duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield ""


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _secretary(persona_marker: str) -> Any:
    """A secretary manifest whose system_prompt carries a unique marker.
    DNAPromptInjector.inject emits manifest.system_prompt verbatim, so the marker
    reaches the assembled prompt and is a precise probe for WHICH persona is in
    it."""
    base = OwlRegistry.with_default_secretary().get("secretary")
    return base.model_copy(update={"system_prompt": f"{base.system_prompt}\n{persona_marker}"})


def _make_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b", tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _build_services(
    provider: OpenAIProvider, owl_registry: OwlRegistry, db: DbPool,
    tool_registry: ToolRegistry | None = None,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    preg.register_mock("router", _RoutingProvider(), tier="fast")
    preg.register_mock("judge-standard", _JudgeProvider(), tier="standard")
    preg.register_mock("judge-local", _JudgeProvider(), tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        db_pool=db,
        # The REAL store against the REAL database — the point of this file.
        session_prompt_store=SessionPromptStore(db),
        tool_registry=tool_registry,
    )


async def _drive(
    backend: AsyncioBackend, scanner: GatewayScanner, text: str, *,
    trace_id: str, conversation_id: str = RUN,
) -> PipelineState:
    """One user message, all the way through the real gateway path."""
    msg = IngressMessage(
        text=text, session_key=LANE, channel="cli", trace_id=trace_id,
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl", f"expected owl route, got {decision.route!r}"
    state = PipelineState(
        trace_id=trace_id,
        session_key=LANE,
        conversation_id=conversation_id,
        input_text=decision.stripped_text if decision.stripped_text is not None else text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    return await backend.run(state)


def _system_messages(client: _FakeClient) -> list[str]:
    """The system message from every provider invocation, in order."""
    out: list[str] = []
    for call in client.chat.completions.calls:
        systems = [m for m in call if m.get("role") == "system"]
        assert systems, f"no system message reached the provider: {call!r}"
        out.append(str(systems[0]["content"]))
    return out


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_two_turns_of_one_conversation_send_a_byte_identical_system_prompt(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """INVARIANT I1, as a business requirement on the real path.

    The persona changes between the turns. A frozen prompt ignores that until
    the next conversation; a rebuilt one would pick it up immediately, so this
    assertion can actually fail.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeClient([_FakeResponse(_FakeMessage("Sure.", tool_calls=None))])
    provider = _make_provider(client)

    registry = OwlRegistry.with_default_secretary()
    registry.replace(_secretary(_PERSONA_BEFORE))

    services = _build_services(provider, registry, tmp_db)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hello there", trace_id="d011-t1")

    # The owl evolves mid-conversation — exactly what nightly evolution does.
    registry.replace(_secretary(_PERSONA_AFTER))

    await _drive(backend, scanner, "and something completely different", trace_id="d011-t2")

    systems = _system_messages(client)
    assert len(systems) >= 2, (
        f"expected at least two provider invocations, got {len(systems)}"
    )

    # === I1: byte-identical, which is what the prefix cache requires ==========
    first_diff = next(
        (i for i, (a, b) in enumerate(zip(systems[0], systems[1], strict=False)) if a != b),
        min(len(systems[0]), len(systems[1])),
    )
    assert systems[0] == systems[1], (
        "I1 FAIL: the two turns sent DIFFERENT system prompts, so the provider's "
        f"automatic prefix cache cannot hit. First difference at char {first_diff}: "
        f"{systems[0][first_diff:first_diff + 60]!r} vs "
        f"{systems[1][first_diff:first_diff + 60]!r}"
    )

    # === The assertion BITES: turn 2 kept the ORIGINAL persona ================
    assert _PERSONA_BEFORE in systems[1], (
        "the frozen prompt lost the persona it was built with"
    )
    assert _PERSONA_AFTER not in systems[1], (
        "I1 FAIL (and the freeze is not engaged): turn 2 picked up the persona "
        "change made mid-conversation, which means the prompt was REBUILT rather "
        "than reused. Bakir's Q14: persona is frozen for the life of a "
        "conversation; evolution lands at the next rollover."
    )


async def test_a_new_conversation_picks_up_the_evolved_persona(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The other half of Q14, and the guard against a cache that is simply stuck.

    Freezing is only correct if the boundary actually releases it. Without this,
    a prompt store that never invalidated anything would pass the test above.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeClient([_FakeResponse(_FakeMessage("Sure.", tool_calls=None))])
    provider = _make_provider(client)

    registry = OwlRegistry.with_default_secretary()
    registry.replace(_secretary(_PERSONA_BEFORE))

    services = _build_services(provider, registry, tmp_db)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "first conversation", trace_id="d011-r1")

    registry.replace(_secretary(_PERSONA_AFTER))

    # A NEW incarnation — what daily rollover mints.
    await _drive(
        backend, scanner, "next day", trace_id="d011-r2", conversation_id=NEXT_RUN,
    )

    systems = _system_messages(client)
    assert _PERSONA_BEFORE in systems[0]
    assert _PERSONA_AFTER in systems[-1], (
        "the rollover did not release the frozen prompt — deferred learning would "
        "never arrive, which makes the daily boundary cosmetic"
    )


async def test_a_tool_still_dispatches_on_the_frozen_turn(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """INVARIANT I3 — no tool the owl owns becomes invisible.

    This is the concrete form of DEBT-22's fear. The old code dropped the
    ``ACTION:`` protocol on a tool-free turn; had that conditional survived into
    a FROZEN prompt, a conversation opening with a chat turn would have carried a
    protocol-less prompt for its entire life and lost tool use until the next
    rollover. Turn 1 here is deliberately conversational, and the tool is
    dispatched on turn 2 — off the CACHED prompt, not a fresh one.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeClient([
        # Turn 1 — a plain conversational reply, no tool.
        _FakeResponse(_FakeMessage("Hello!", tool_calls=None)),
        # Turn 2 — the model calls the tool off the frozen prompt...
        _FakeResponse(_FakeMessage(
            f"Let me check.\nACTION: {_TOOL_NAME}\n```json\n{{\"q\": \"x\"}}\n```",
            tool_calls=None,
        )),
        # ...then answers with the observation.
        _FakeResponse(_FakeMessage(f"Result: {_TOOL_MARKER}", tool_calls=None)),
    ])
    provider = _make_provider(client)

    registry = OwlRegistry.with_default_secretary()
    registry.replace(_secretary(_PERSONA_BEFORE))

    tool = _ProbeTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    services = _build_services(provider, registry, tmp_db, tool_registry=tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hi", trace_id="d011-i3-t1")
    await _drive(backend, scanner, "now do the thing", trace_id="d011-i3-t2")

    systems = _system_messages(client)

    # The frozen prompt still teaches the calling protocol, on every turn.
    for i, text in enumerate(systems):
        assert "ACTION:" in text, (
            f"I3 FAIL: provider invocation {i} received a system prompt with no "
            "calling protocol — a frozen prompt that dropped it would disable "
            "tool use for the whole conversation (DEBT-22)."
        )

    assert tool.calls, (
        "I3 FAIL: the tool was never dispatched on the second turn, whose prompt "
        "came from the cache. A tool the owl owns became invisible."
    )
