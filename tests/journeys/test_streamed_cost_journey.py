"""T5 (F119) — streaming paths record cost via the single recording site.

The conversational reply path streams deltas; before the fix the streamed round
recorded NO cost, so per-turn budget enforcement under-counted and could be
bypassed on cheap conversational turns. The fix records the final usage in a
``try/finally`` at generator exit, Ollama-tolerant.

Drives the REAL provider ``stream()`` over a fake SDK transport (mocking ONLY the
transport) + a REAL ``CostTracker`` keyed by ``TraceContext``. Asserts:

* a usage-bearing stream records spend (``turn_cost_usd(trace) > 0`` and a
  ``cost_records`` row exists);
* an Ollama-style stream with NO usage chunk records zero and never raises (guards
  the real dev hardware).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.infra.trace import TraceContext
from stackowl.providers.base import Message
from stackowl.providers.cost_tracker import CostTracker
from stackowl.providers.openai_provider import OpenAIProvider

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Fake OpenAI streaming transport — yields delta chunks then (optionally) a
# final empty-choices chunk carrying ``.usage`` (OpenAI stream_options behavior).
# Ollama/compatible endpoints omit the usage chunk entirely.
# --------------------------------------------------------------------------- #


class _Delta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.delta = _Delta(content)


class _Usage:
    prompt_tokens = 1000
    completion_tokens = 2000


class _Chunk:
    def __init__(self, *, content: str | None, usage: _Usage | None, with_choice: bool = True) -> None:
        self.choices = [_Choice(content)] if with_choice else []
        self.usage = usage
        # A priced model id so the recorded usage estimates a non-zero cost (a real
        # streamed reply reports its model on the trailing usage chunk).
        self.model = "gpt-4o-mini"


class _FakeStream:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[_Chunk]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[_Chunk]:
        for c in self._chunks:
            yield c


class _FakeCompletions:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks
        self.create_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> _FakeStream:
        self.create_kwargs = kwargs
        return _FakeStream(self._chunks)


class _FakeClient:
    def __init__(self, chunks: list[_Chunk]) -> None:
        comp = _FakeCompletions(chunks)

        class _Chat:
            def __init__(self) -> None:
                self.completions = comp

        self.chat = _Chat()
        self._completions = comp


def _provider(chunks: list[_Chunk]) -> tuple[OpenAIProvider, _FakeClient]:
    cfg = ProviderConfig(
        name="streamer",
        protocol="openai",
        enabled=True,
        base_url="http://localhost:9/v1",
        default_model="fake-openai-model",
        tier="fast",
    )
    p = OpenAIProvider(cfg, api_key="k")
    client = _FakeClient(chunks)
    p._client = client  # type: ignore[assignment]
    return p, client


def _cost_tracker(db: DbPool) -> CostTracker:
    return CostTracker(db=db, event_bus=EventBus())


async def test_streamed_reply_records_cost(tmp_db: DbPool) -> None:
    """A usage-bearing stream records spend through the single recording site."""
    chunks = [
        _Chunk(content="Hello ", usage=None),
        _Chunk(content="world", usage=None),
        # Final empty-choices chunk carrying usage (OpenAI include_usage).
        _Chunk(content=None, usage=_Usage(), with_choice=False),
    ]
    provider, client = _provider(chunks)
    tracker = _cost_tracker(tmp_db)
    provider.set_cost_tracker(tracker)

    trace = "trace-stream-cost-1"
    token = TraceContext.start(trace_id=trace)
    try:
        out = "".join([d async for d in provider.stream([Message(role="user", content="hi")], model="")])
    finally:
        TraceContext.reset(token)

    assert out == "Hello world"
    # F119 — the streamed round recorded spend (was 0 before the fix).
    assert tracker.turn_cost_usd(trace) > 0.0, "streamed reply recorded NO cost"
    # A cost_records row was persisted for this trace.
    rows = await tmp_db.fetch_all(
        "SELECT trace_id FROM cost_records WHERE trace_id = ?", (trace,)
    )
    assert rows, "no cost_records row persisted for the streamed reply"
    # The single recording site requested include_usage from the transport.
    assert client._completions.create_kwargs.get("stream_options") == {"include_usage": True}


async def test_streamed_cost_missing_usage_records_zero_no_error(tmp_db: DbPool) -> None:
    """Ollama path: no usage chunk → stream completes, zero recorded, no exception."""
    chunks = [
        _Chunk(content="ollama ", usage=None),
        _Chunk(content="reply", usage=None),
        # NO final usage chunk — Ollama/compatible endpoints omit it.
    ]
    provider, _client = _provider(chunks)
    tracker = _cost_tracker(tmp_db)
    provider.set_cost_tracker(tracker)

    trace = "trace-stream-cost-ollama"
    token = TraceContext.start(trace_id=trace)
    try:
        out = "".join([d async for d in provider.stream([Message(role="user", content="hi")], model="")])
    finally:
        TraceContext.reset(token)

    assert out == "ollama reply"  # the stream completed normally
    assert tracker.turn_cost_usd(trace) == 0.0  # nothing recorded, no crash


# --------------------------------------------------------------------------- #
# Anthropic + Gemini streamed-cost coverage — same single-recording-site fix.
# --------------------------------------------------------------------------- #


async def test_anthropic_stream_records_cost(tmp_db: DbPool) -> None:
    from stackowl.providers.anthropic_provider import AnthropicProvider

    class _Usage:
        input_tokens = 1000
        output_tokens = 2000

    class _Final:
        usage = _Usage()
        model = "claude-haiku-4-5-20251001"

    class _Stream:
        async def _text(self) -> AsyncIterator[str]:
            for t in ("anthropic ", "stream"):
                yield t

        @property
        def text_stream(self) -> AsyncIterator[str]:
            return self._text()

        async def get_final_message(self) -> _Final:
            return _Final()

        async def __aenter__(self) -> _Stream:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

    class _Messages:
        def stream(self, **_kwargs: Any) -> _Stream:
            return _Stream()

    class _Client:
        messages = _Messages()

    cfg = ProviderConfig(
        name="a", protocol="anthropic", enabled=True,
        default_model="claude-haiku-4-5-20251001", tier="fast",
    )
    p = AnthropicProvider(cfg, api_key="k")
    p._client = _Client()  # type: ignore[assignment]
    tracker = _cost_tracker(tmp_db)
    p.set_cost_tracker(tracker)

    trace = "trace-stream-anthropic"
    token = TraceContext.start(trace_id=trace)
    try:
        out = "".join([d async for d in p.stream([Message(role="user", content="hi")], model="")])
    finally:
        TraceContext.reset(token)

    assert out == "anthropic stream"
    assert tracker.turn_cost_usd(trace) > 0.0, "anthropic streamed reply recorded NO cost"


async def test_gemini_stream_records_cost(tmp_db: DbPool) -> None:
    from stackowl.providers.gemini_provider import GeminiProvider

    class _Usage:
        prompt_token_count = 1000
        candidates_token_count = 2000

    class _Chunk:
        def __init__(self, text: str, usage: Any) -> None:
            self.text = text
            self.usage_metadata = usage

    async def _gen() -> AsyncIterator[_Chunk]:
        yield _Chunk("gemini ", None)
        yield _Chunk("stream", _Usage())

    class _Models:
        async def generate_content_stream(self, **_kwargs: Any) -> AsyncIterator[_Chunk]:
            return _gen()

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    cfg = ProviderConfig(
        name="g", protocol="gemini", enabled=True,
        default_model="gemini-2.0-flash", tier="fast",
    )
    p = GeminiProvider.__new__(GeminiProvider)  # bypass genai.Client construction
    p._name = "g"  # type: ignore[attr-defined]
    p._config = cfg  # type: ignore[attr-defined]
    p._client = _Client()  # type: ignore[attr-defined]
    tracker = _cost_tracker(tmp_db)
    p.set_cost_tracker(tracker)

    trace = "trace-stream-gemini"
    token = TraceContext.start(trace_id=trace)
    try:
        out = "".join([d async for d in p.stream([Message(role="user", content="hi")], model="")])
    finally:
        TraceContext.reset(token)

    assert out == "gemini stream"
    assert tracker.turn_cost_usd(trace) > 0.0, "gemini streamed reply recorded NO cost"
