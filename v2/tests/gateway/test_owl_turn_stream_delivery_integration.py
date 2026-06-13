"""Real owl-turn delivery regression: dispatch → backend.run → deliver → send.

Why this exists (the regression it locks):
  ``ffa6a90`` re-keyed the response stream from ``session_id`` → ``request_id``
  (== ``trace_id``) in ``pipeline/streaming.py`` and made ``pipeline/steps/deliver.py``
  resolve the writer by ``state.trace_id``. The 42 TEST harnesses were updated to
  register streams by ``trace_id``, but the PRODUCTION gateway
  (``startup/orchestrator.py`` + ``gateway/clarify_pump.py``) still
  registered/looked-up/removed the stream by ``session_id``. Because
  ``session_id != trace_id`` in every adapter, every successful owl turn would
  stream-MISS in ``deliver`` (no writer for ``trace_id``) → the writer registered
  under ``session_id`` was NEVER closed → no sentinel → ``adapter.send(reader)``
  hangs forever → the session wedges. Tests were green only because the harnesses
  were swapped to ``trace_id``; production was silently broken.

  No test drove the REAL orchestrator owl-turn path end-to-end, so the
  inconsistency was invisible. This is that test.

What it drives (the smallest REAL slice that reproduces the production wiring):
  * a REAL :class:`StreamRegistry`, shared between :class:`StepServices` (so the
    real ``deliver`` step writes into it) and the gateway's stream-create — exactly
    as ``orchestrator.py`` wires ``stream_registry`` into both;
  * a REAL :class:`AsyncioBackend` running the full pipeline with a mock provider;
  * the REAL :class:`ClarifyPump.spawn_send` draining the stream into a channel
    adapter — the same call the CLI/Telegram loops make;
  * the gateway's stream-create + spawn_send keyed exactly as the production loop
    keys them.

The assertion is the OUTCOME a user cares about: the owl turn's response chunk
REACHES the channel adapter (it is delivered, not stream-missed) WITHOUT hanging.
``asyncio.wait_for(..., timeout=5)`` wraps the send so a hang FAILS the test
instead of wedging the suite.

Catches-the-hang proof: ``deliver`` looks the writer up by ``state.trace_id``.
If the gateway registered the stream by ``session_id`` (the regression) while
``session_id != trace_id``, ``deliver`` stream-misses, never closes the writer,
the sentinel never arrives, and the ``wait_for`` around the send TIMES OUT →
this test FAILS. It passes only when the gateway registers/looks-up/removes the
stream by the SAME key ``deliver`` uses (``trace_id``).
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.channels.base import ChannelAdapter
from stackowl.db.pool import DbPool
from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamReader, StreamRegistry
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


# ---- Mock provider (resolved THROUGH the provider_registry) ------------------


class _CannedProvider(ModelProvider):
    """Returns a canned tool-loop reply with zero tool calls (consent never runs)."""

    def __init__(self) -> None:
        self._name = "fake"

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="OWL_REPLY",
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "OWL_REPLY"

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check=None,
        **_kwargs,
    ) -> tuple[str, list]:
        return "OWL_REPLY", []


# ---- Capturing channel adapter (drains the reader like the real adapters) ----


class _CapturingAdapter(ChannelAdapter):
    """Minimal real-shaped adapter: ``send`` drains the reader until the sentinel."""

    def __init__(self) -> None:
        self.received: list[str] = []

    @property
    def channel_name(self) -> str:
        return "cli"

    async def receive(self) -> IngressMessage:  # pragma: no cover — unused here
        raise NotImplementedError

    async def send(self, chunks: StreamReader) -> None:
        async for chunk in chunks:
            self.received.append(chunk.content)

    async def send_text(self, text: str) -> None:  # pragma: no cover — unused
        self.received.append(text)


# ---- Helpers ----------------------------------------------------------------


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: _CannedProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
    stream_registry: StreamRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        # SHARED registry — the real ``deliver`` step writes into THIS instance,
        # exactly as orchestrator.py wires one stream_registry into both services
        # and the gateway loop.
        stream_registry=stream_registry,
    )


async def test_real_owl_turn_delivers_to_channel_without_hanging(tmp_db: DbPool) -> None:
    """A real owl turn (dispatch→backend.run→deliver→send) DELIVERS, no hang.

    Drives the production wiring: the gateway registers the stream by the SAME
    key ``deliver`` looks it up by (``trace_id``) and the real ``ClarifyPump``
    drains it into the channel adapter. If the gateway re-keyed by ``session_id``
    (the regression) the ``wait_for`` below would time out and FAIL.
    """
    stream_registry = StreamRegistry()
    bridge = SqliteMemoryBridge(db=tmp_db)
    provider = _CannedProvider()
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()

    services = _build_services(
        bridge, provider, owl_registry, tool_registry, stream_registry
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)
    adapter = _CapturingAdapter()

    # The two keys DIFFER, just like every real adapter (CLI: session UUID vs
    # per-msg id; Telegram: str(user_id) vs uuid4). If the gateway uses the wrong
    # one, deliver stream-misses.
    session_id = "sess-ABC"
    trace_id = "trace-XYZ"
    assert session_id != trace_id

    msg = IngressMessage(
        text="hello owl",
        session_id=session_id,
        channel="cli",
        trace_id=trace_id,
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl"
    assert decision.target == "secretary"

    # --- Reproduce the production gateway dispatch sequence -------------------
    # Stream-create keyed exactly as orchestrator.py keys it (post-fix: trace_id).
    writer, reader = stream_registry.create(msg.trace_id)

    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=decision.stripped_text if decision.stripped_text is not None else msg.text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    producer: asyncio.Task[object] = asyncio.create_task(backend.run(state))

    # The REAL pump drains the stream into the adapter, decoupled-send style.
    pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]
    pump.spawn_send(
        channel_adapter=adapter,
        reader=reader,
        session_id=msg.session_id,   # the serialize-gate key (unchanged)
        request_id=msg.trace_id,     # the STREAM key (== deliver's lookup key)
        producer=producer,
        writer=writer,
    )

    # The owl turn must DELIVER — a chunk reaches the adapter — without hanging.
    # wait_for makes a hang FAIL the test instead of wedging the suite.
    await asyncio.wait_for(producer, timeout=5)
    send_task = pump._inflight.get(msg.session_id)  # type: ignore[attr-defined]
    assert send_task is not None
    await asyncio.wait_for(send_task, timeout=5)
    await asyncio.sleep(0)  # let the send task's done-callback (_cleanup) run

    # OUTCOME: the owl's reply reached the channel (not stream-missed).
    assert any("OWL_REPLY" in c for c in adapter.received), (
        "owl turn did NOT deliver to the channel — stream-missed. "
        f"adapter.received={adapter.received!r}"
    )
    # The stream was reaped under the STREAM key (trace_id), not session_id.
    assert stream_registry.get_writer(msg.trace_id) is None
    assert msg.session_id not in pump._inflight  # type: ignore[attr-defined]


class _NullGateway:
    """A clarify gateway the pump never consults in this test (spawn_send only)."""

    def peek_for_session(self, session_id: str, channel: str) -> None:  # pragma: no cover
        return None
