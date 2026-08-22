"""D01.4 — an edit you just made reaches the next reply, on the REAL path.

THE BUSINESS REQUIREMENT, as a user would state it:

    "I changed my owl. The next thing it says should reflect that."

Until this item, it did not — for up to twelve hours. D01.1 froze the system
prompt for the life of a session and nothing in the tree could clear it, so an
edit made mid-conversation stayed invisible until D01.7 rolled the session at
04:00. That is not a hypothesis: the assertion below is the exact inverse of
`test_two_turns_of_one_conversation_send_a_byte_identical_system_prompt` in
test_d011_frozen_prompt_gateway_integration.py, which passes on the pre-D01.4
tree by asserting the persona change is NOT picked up.

So this test is guaranteed to be able to fail — it failed by design before the
invalidation seam existed, which is the property D01.1 paid to learn the hard
way when an intent-class assertion passed for a whole slice while nothing could
differ.

Only the AI provider is faked. The scanner, the router decision, the pipeline
steps, the owl registry, the real SessionPromptStore and the real database are
all live. The store tests prove invalidate_owl() clears the right rows; they
prove nothing about whether the command CALLS it, and a wiring omission would
leave every one of them green.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.owls_command import OwlsCommand
from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.sessions.prompt_store import SessionPromptStore

pytestmark = pytest.mark.asyncio

LANE = "owl:scout:cli:dm:d014"
RUN = "20260728_090000_d014aaaa"

_ROLE_BEFORE = "PERSONA-MARKER-ORIGINAL-3a71"
_ROLE_AFTER = "PERSONA-MARKER-EDITED-8c42"


# --------------------------------------------------------------------------- #
# Fakes — the AI provider only.
# --------------------------------------------------------------------------- #


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeStreamChoice(content)]
        self.usage = None
        self.model = "gemma4:e4b"


class _FakeStream:
    """The pipeline's answer path STREAMS — the same discovery D01.2's test stage
    made about the Anthropic provider, here in the OpenAI one. A create()-only
    fake blows up with 'async for requires __aiter__', which is how this was
    found rather than assumed."""

    def __aiter__(self) -> _FakeStream:
        self._chunks = iter([_FakeChunk("Sure."), _FakeChunk(None)])
        return self

    async def __anext__(self) -> _FakeChunk:
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append([dict(m) for m in kwargs["messages"]])
        if kwargs.get("stream"):
            return _FakeStream()
        return _FakeResponse(_FakeMessage("Sure."))


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat(_FakeCompletions())


class _RoutingProvider(ModelProvider):
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
            content="scout", input_tokens=1, output_tokens=1,
            model="routing-fake", provider_name="routing-fake", duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "scout"


class _JudgeProvider(ModelProvider):
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


def _scout(role: str) -> OwlAgentManifest:
    """The marker goes in ``system_prompt``, NOT ``role``.

    Found by the test failing: DNAPromptInjector emits ``manifest.system_prompt``
    verbatim into the persona, while ``role`` only ever appears in the OWLS BLOCK
    — which lists the OTHER owls, never the one whose prompt this is. Editing
    --role therefore changes nothing visible in scout's own prompt, and a probe
    on it would have failed forever while the code was correct.
    """
    return OwlAgentManifest(
        name="scout", role=role, system_prompt=f"Be helpful. {role}",
        model_tier="fast", dna=OwlDNA(),
    )


def _registry(role: str) -> OwlRegistry:
    registry = OwlRegistry.with_default_secretary()
    registry.register(_scout(role))
    return registry


def _provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b", tier="powerful",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _services(provider: OpenAIProvider, registry: OwlRegistry, db: DbPool) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("scout", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    preg.register_mock("router", _RoutingProvider(), tier="fast")
    preg.register_mock("judge-standard", _JudgeProvider(), tier="standard")
    preg.register_mock("judge-local", _JudgeProvider(), tier="local")
    return StepServices(
        provider_registry=preg, owl_registry=registry, db_pool=db,
        session_prompt_store=SessionPromptStore(db),
    )


async def _drive(
    backend: AsyncioBackend, scanner: GatewayScanner, text: str, *, trace_id: str
) -> PipelineState:
    msg = IngressMessage(text=text, session_key=LANE, channel="cli", trace_id=trace_id)
    decision = scanner.scan(msg)
    state = PipelineState(
        trace_id=trace_id, session_key=LANE, conversation_id=RUN,
        input_text=decision.stripped_text if decision.stripped_text is not None else text,
        channel=msg.channel, owl_name="scout", pipeline_step="start", interactive=True,
    )
    return await backend.run(state)


def _system_messages(client: _FakeClient) -> list[str]:
    out: list[str] = []
    for call in client.chat.completions.calls:
        systems = [m for m in call if m.get("role") == "system"]
        assert systems, f"no system message reached the provider: {call!r}"
        out.append(str(systems[0]["content"]))
    return out


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_an_edit_reaches_the_very_next_reply(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The item's whole promise, in one assertion — and it FAILED by design
    before the invalidation seam existed."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeClient()
    registry = _registry(_ROLE_BEFORE)
    backend = AsyncioBackend(services=_services(_provider(client), registry, tmp_db))
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hello there", trace_id="d014-t1")

    # The user edits the owl mid-conversation, through the REAL command.
    reply = await OwlsCommand(owl_registry=registry, db=tmp_db)._edit(  # noqa: SLF001
        f"scout --system-prompt 'Be helpful. {_ROLE_AFTER}'"
    )
    assert reply.startswith("✓"), reply

    await _drive(backend, scanner, "and now?", trace_id="d014-t2")

    systems = _system_messages(client)
    assert len(systems) >= 2, f"expected two provider calls, got {len(systems)}"
    assert _ROLE_BEFORE in systems[0], "turn 1 should carry the original role"
    assert _ROLE_AFTER in systems[-1], (
        "the edit did NOT reach the next reply — the frozen prompt was not "
        "invalidated, so the change stays invisible until the session rolls over"
    )


async def test_the_turn_after_an_edit_cold_builds_and_refreezes(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Invalidation reuses the existing miss path: the row is cleared, then the
    rebuild stores the NEW prompt, so the freeze keeps working afterwards."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeClient()
    registry = _registry(_ROLE_BEFORE)
    backend = AsyncioBackend(services=_services(_provider(client), registry, tmp_db))
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hello there", trace_id="d014-r1")
    await OwlsCommand(owl_registry=registry, db=tmp_db)._edit(  # noqa: SLF001
        f"scout --system-prompt 'Be helpful. {_ROLE_AFTER}'"
    )

    store = SessionPromptStore(tmp_db)
    assert await store.load(
        session_key=LANE, owl_name="scout", conversation_id=RUN
    ) is None, "the edit should have cleared the frozen row"

    await _drive(backend, scanner, "and now?", trace_id="d014-r2")

    refrozen = await store.load(session_key=LANE, owl_name="scout", conversation_id=RUN)
    assert refrozen is not None, "the rebuild must re-freeze, or every later turn rebuilds"
    assert _ROLE_AFTER in refrozen.prompt_text
