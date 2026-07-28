"""D01.2 — cache breakpoints, proven on the REAL gateway path.

WHAT THIS PROVES, and why the unit tests were not enough.
``tests/providers/test_cache_control.py`` proves the marker layout in isolation
and ``test_anthropic_cache_breakpoints.py`` proves the provider seam. Neither
establishes that the whole chain is wired: gateway scan -> route ->
PipelineState -> backend -> the pipeline -> assemble's frozen prompt -> the
Anthropic provider -> a request on the wire that actually carries markers, and a
usage response that actually reaches the REAL CostTracker and the REAL
CacheProbeStore against a REAL database.

The business requirement, as a user would state it:

    "When my assistant talks to an Anthropic backend, the request it sends is
     marked so the provider bills a long conversation at the cached rate — and
     what the model READS is not changed by that."

Only the AI provider is faked. The scanner, the router decision, the pipeline
steps, the owl registry, the prompt store, the cost tracker, the probe store and
the database are all real.

THE ASSERTION THAT DOES THE WORK is not "markers are present" — that one passes
on almost anything. It is that the text the model reads is byte-identical to the
text it would have read unmarked. Marking rewrites the system prompt from a
string into a block list and rewrites message contents in place; getting that
wrong silently changes the model's input to buy a cache entry, which is the one
trade that is never worth making. That assertion can fail, and D01.1 paid for
the lesson that an assertion which cannot fail reads as coverage without being
coverage.

MUTATION-TESTED 2026-07-27 rather than assumed — see the item's test_result in
progress.yml for which mutation kills which test.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.cache_probe_store import CacheProbeStore
from stackowl.providers.cost_tracker import CostTracker
from stackowl.providers.registry import ProviderRegistry
from stackowl.sessions.prompt_store import SessionPromptStore

pytestmark = pytest.mark.asyncio

LANE = "owl:secretary:cli:dm:d012"
# See DEBT-27: assemble gates BOTH the cache read and the freeze write on a
# non-empty session_id, so a blank one would silently disable the frozen prompt
# this item exists to claim a discount for.
RUN = "20260727_100000_d012aaaa"


# --------------------------------------------------------------------------- #
# Fakes — the AI provider only.
# --------------------------------------------------------------------------- #


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text
        self.type = "text"


class _Usage:
    """A usage object shaped from the Anthropic API DOCUMENTATION.

    NOT captured from the real API — this box runs no Anthropic backend. A pass
    here proves we READ the fields the documentation names; it does not prove the
    live service populates them.
    """

    def __init__(self, cache_read: int, cache_creation: int) -> None:
        self.input_tokens = 5000
        self.output_tokens = 40
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_creation


class _Response:
    def __init__(self, text: str, usage: _Usage) -> None:
        self.content = [_Block(text)]
        self.usage = usage
        self.model = "claude-opus-5"
        self.stop_reason = "end_turn"


class _CountTokens:
    def __init__(self, input_tokens: int = 6000) -> None:
        self.calls: list[dict[str, Any]] = []
        self._n = input_tokens

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("C", (), {"input_tokens": self._n})()


class _FakeStream:
    """The SDK's streaming context manager, in the shape the provider uses.

    THE PIPELINE'S ANSWER PATH STREAMS. That is not a detail — it is why the
    cleanup stage's finding mattered: for a whole slice only the non-streaming
    ``_record_usage_safe`` fed the probe store, so on the path a real turn
    actually takes, nothing was recorded at all. This fake exists because the
    first run of this file called ``create`` zero times and ``stream`` once.
    """

    def __init__(self, usage: _Usage) -> None:
        self._usage = usage

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    @property
    async def text_stream(self) -> Any:  # pragma: no cover - replaced below
        raise NotImplementedError

    async def get_final_message(self) -> _Response:
        return _Response("Sure, done.", self._usage)


class _FakeAnthropicMessages:
    """Records every outgoing request verbatim, and answers with a canned usage.

    Both entry points are recorded into ONE list: the provider routes
    ``create()`` and ``stream()`` through the same chokepoint, so a test that
    watched only one of them would pass while the other went out unmarked.
    """

    def __init__(self, cache_read: int = 0, cache_creation: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.count_tokens = _CountTokens()
        self._usage = _Usage(cache_read, cache_creation)

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        self.create_calls.append(kwargs)
        return _Response("Sure, done.", self._usage)

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        self.stream_calls.append(kwargs)
        stream = _FakeStream(self._usage)

        async def _texts() -> Any:
            for chunk in ("Sure, ", "done."):
                yield chunk

        type(stream).text_stream = property(lambda _self: _texts())  # type: ignore[assignment]
        return stream


class _RoutingProvider(ModelProvider):
    """FAST tier — the triage router, so it does not consume the answer path."""

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
    """STANDARD/LOCAL tiers — the give-up judge rules DELIVERED."""

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


def _anthropic(messages_fake: _FakeAnthropicMessages) -> AnthropicProvider:
    config = ProviderConfig(
        name="anthropic-main", protocol="anthropic",
        default_model="claude-opus-5", tier="powerful",
        max_output_tokens=4096,
    )
    provider = AnthropicProvider(config, api_key="not-used")
    provider._client.messages = messages_fake  # type: ignore[assignment]
    return provider


def _build_services(
    provider: AnthropicProvider, owl_registry: OwlRegistry, db: DbPool,
    *, cost_tracker: CostTracker | None = None,
    probe_store: CacheProbeStore | None = None,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    preg.register_mock("router", _RoutingProvider(), tier="fast")
    preg.register_mock("judge-standard", _JudgeProvider(), tier="standard")
    preg.register_mock("judge-local", _JudgeProvider(), tier="local")
    if cost_tracker is not None:
        preg.set_cost_tracker(cost_tracker)
    if probe_store is not None:
        preg.set_cache_probe_store(probe_store)
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        db_pool=db,
        session_prompt_store=SessionPromptStore(db),
    )


async def _drive(
    backend: AsyncioBackend, scanner: GatewayScanner, text: str, *, trace_id: str
) -> PipelineState:
    msg = IngressMessage(text=text, session_key=LANE, channel="cli", trace_id=trace_id)
    decision = scanner.scan(msg)
    assert decision.route == "owl", f"expected owl route, got {decision.route!r}"
    state = PipelineState(
        trace_id=trace_id,
        session_key=LANE,
        session_id=RUN,
        input_text=decision.stripped_text if decision.stripped_text is not None else text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    return await backend.run(state)


def _markers(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "cache_control":
                found.append(value)
            else:
                found.extend(_markers(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_markers(item))
    return found


def _visible_text(value: Any) -> str:
    """Every piece of text the MODEL would read, markers stripped out.

    Only the ``text`` field of a content block counts. An earlier version of this
    helper concatenated every value in the dict, which folded the block's own
    ``"type": "text"`` discriminator into the output and made the comparison fail
    with a spurious leading "text" — a bug in the probe, not in the code it was
    probing.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_visible_text(v) for v in value)
    if isinstance(value, dict):
        # Two dict shapes reach here: a MESSAGE ({"role", "content"}) and a
        # CONTENT BLOCK ({"type", "text"}). Handle both, and never fold in the
        # block's own "type" discriminator.
        if "text" in value:
            return _visible_text(value["text"])
        if "content" in value:
            return _visible_text(value["content"])
    return ""


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_a_real_turn_puts_cache_markers_on_the_wire(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The business requirement: a real conversation turn is sent marked."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    fake = _FakeAnthropicMessages()
    registry = OwlRegistry.with_default_secretary()
    services = _build_services(_anthropic(fake), registry, tmp_db)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hello there", trace_id="d012-t1")

    assert fake.calls, "the Anthropic provider was never called"
    for call in fake.calls:
        placed = _markers(call)
        assert placed, "a real turn reached the provider with NO cache markers"
        # I1 — the hard API limit, on the real path.
        assert len(placed) <= 4, f"{len(placed)} markers exceeds the budget of 4"


async def test_marking_does_not_change_what_the_model_reads(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """THE assertion that does the work.

    Marking rewrites the system prompt from a string into a block list and
    rewrites message contents. If that rewriting ever drops, reorders or mangles
    text, we would have silently changed the model's input to buy a cache entry —
    the one trade that is never worth making.

    The frozen prompt (D01.1) is the reference: whatever assemble stored in the
    database is exactly what the model must end up reading.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    fake = _FakeAnthropicMessages()
    registry = OwlRegistry.with_default_secretary()
    services = _build_services(_anthropic(fake), registry, tmp_db)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hello there", trace_id="d012-t2")

    stored = await SessionPromptStore(tmp_db).load(
        session_key=LANE, owl_name="secretary", session_id=RUN,
    )
    assert stored is not None, "assemble never froze a prompt — nothing to compare"

    sent = fake.calls[0]
    assert _visible_text(sent["system"]) == stored.prompt_text, (
        "the marked system prompt is not byte-identical to the frozen one"
    )
    # And the user's own words survived the rewrite.
    assert "hello there" in _visible_text(sent["messages"])


async def test_a_confirmed_cache_reading_is_persisted_on_the_real_path(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The probe reaches the REAL store against the REAL database."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    fake = _FakeAnthropicMessages(cache_read=0, cache_creation=2712)
    registry = OwlRegistry.with_default_secretary()
    probes = CacheProbeStore(tmp_db)
    services = _build_services(
        _anthropic(fake), registry, tmp_db,
        cost_tracker=CostTracker(tmp_db, EventBus()), probe_store=probes,
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hello there", trace_id="d012-t3")

    probe = await probes.load(provider_name="anthropic-main", model="claude-opus-5")
    assert probe is not None, "a confirmed positive was not persisted"
    assert probe.cache_creation_tokens == 2712


async def test_a_silent_gateway_never_gets_recorded_as_confirmed(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """I5 on the real path — the invariant the whole design hangs on.

    A backend that strips usage cache fields reports zero on turn one, exactly
    like a dead marker would. Persisting that would disable the feature forever,
    across restarts, with no error. D01.6 measured that this is not hypothetical:
    NeraAiRaw reports no cache fields in any accepted shape.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    fake = _FakeAnthropicMessages(cache_read=0, cache_creation=0)
    registry = OwlRegistry.with_default_secretary()
    probes = CacheProbeStore(tmp_db)
    services = _build_services(
        _anthropic(fake), registry, tmp_db,
        cost_tracker=CostTracker(tmp_db, EventBus()), probe_store=probes,
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=registry)

    await _drive(backend, scanner, "hello there", trace_id="d012-t4")

    assert await probes.load(
        provider_name="anthropic-main", model="claude-opus-5"
    ) is None, "a zero reading was persisted — the feature could disable itself"
    # ... and marking carried on regardless.
    assert _markers(fake.calls[0]), "marking stopped after a zero reading"
