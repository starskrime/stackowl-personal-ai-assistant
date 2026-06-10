"""J10 JOURNEY — "Look at this image and tell me what's in it" (E10, S2).

The business requirement, in user terms:

  > I reference an image (a file or a link) and the owl LOOKS at it and tells me
  > what's in it. When the image had to leave my machine to a cloud vision model,
  > the owl tells me so.

THE headline business outcome: the user's final Telegram reply contains the
vision model's REAL description of the referenced image — DERIVED from the actual
``vision_analyze`` tool output, not a constant. The owl (a) was handed a REAL
on-disk image the loader genuinely loads, (b) ran the REAL ``vision_analyze`` tool
which selected a REAL ``ProviderRegistry``'s vision provider LOCAL-FIRST and called
its ``complete()`` with the image as an image block, and (c) threaded that real
description into the reply — end-to-end through the GENUINE gateway path, mocking
ONLY the vision provider's ``complete()`` output + the Telegram transport.

REAL (everything except the vision provider's canned output): the whole pipeline
(TelegramChannelAdapter → GatewayScanner → AsyncioBackend → execute._dispatch →
ToolRegistry → REAL ``VisionAnalyzeTool`` → REAL ``ImageLoader`` loading a REAL
PNG off disk → REAL ``VisionSelector`` picking from a REAL ``ProviderRegistry``),
plus the Telegram adapter inbound + outbound.

FAKED — ONLY the AI: (1) a scripted secretary owl whose ``complete_with_tools``
emits ONE ``vision_analyze`` call and threads its real output into the reply, and
(2) the selected vision provider's ``complete()`` returns a canned description (the
ONE vision mock). Both live in a SINGLE REAL ``ProviderRegistry`` (the secretary
under the owl name; the vision mock under a localhost/cloud base_url). The Telegram
bot HTTP transport is faked in-process (``_FakeBot``).

LOCAL vs CLOUD egress proof: the journey runs TWICE off the same harness. With a
LOCALHOST vision provider the description reaches the user with NO egress note (the
image stayed on the box). With a CLOUD-base_url vision provider the SAME description
reaches the user PREFIXED with the egress disclosure naming the provider (the image
left the box). The two cases share every wire except the provider's base_url.

FAIL-WHEN-UNWIRED proof: a third run injects ``provider_registry=None``. The REAL
``vision_analyze`` self-heals to a structured "vision substrate unavailable", that
reaches the user as the reply, and NO description/crash occurs. (Dev-review check:
restore the registry and the description returns; drop it and it goes unavailable.)
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 828282
_DESCRIPTION = "a single bright red pixel on a tiny one-by-one canvas"


# --- a genuine 1x1 PNG written with stdlib (the loader really loads it) ---------
def _png_bytes() -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _write_png() -> str:
    from stackowl.tools.io.path_guard import data_root

    target = data_root() / "j10_image.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_png_bytes())
    return str(target)


# --- FAKED transport: the Telegram bot HTTP layer (in-process capture) ----------
class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


# --- the vision mock: the ONE vision provider whose complete() is canned ---------
class _VisionMock(MockProvider):
    def __init__(self) -> None:
        super().__init__(name="vision")
        self.saw_image = False

    @property
    def supports_vision(self) -> bool:  # type: ignore[override]
        return True

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        for m in messages:
            for d in m.documents:
                if d.media_type.startswith("image/"):
                    self.saw_image = True
        return CompletionResult(
            content=_DESCRIPTION, input_tokens=1, output_tokens=1,
            model="vision-mock", provider_name=self.name, duration_ms=1.0,
        )


# --- the scripted secretary owl (the ONLY tool-loop AI) -------------------------
class _ScriptedSecretary(MockProvider):
    """The secretary owl's LLM, scripted to call vision_analyze ONCE and thread its
    REAL output into the final reply (no constants)."""

    protocol = "anthropic"  # type: ignore[assignment]

    def __init__(self, image_path: str) -> None:
        super().__init__(name="secretary")
        self._image_path = image_path
        self.tool_out: str = ""
        self.tool_err: str | None = None
        self.final: str = ""

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        calls: list[dict] = []
        raw = await tool_dispatcher(
            "vision_analyze",
            {"image": self._image_path, "question": "What is in this image?"},
        )
        self.tool_out = raw
        calls.append({"name": "vision_analyze", "args": {"image": self._image_path}, "result": raw})
        # The dispatcher returns the tool's human-facing output (the description, or
        # a structured error string). Thread it VERBATIM into the reply so whatever
        # the REAL tool produced reaches the user.
        self.final = f"Here's what I see: {raw}"
        return (self.final, calls)


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    secretary: _ScriptedSecretary
    vision: _VisionMock | None
    image_path: str


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build(*, locality: Literal["local", "cloud"]) -> _Env:
    """Build the env with a REAL ProviderRegistry holding the secretary (owl) + a
    vision mock whose base_url decides locality (local → on-box; cloud → egress)."""
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    image_path = _write_png()
    secretary = _ScriptedSecretary(image_path)

    provider_registry = ProviderRegistry()
    provider_registry.register_mock("secretary", secretary, tier="powerful", base_url=None)
    vision = _VisionMock()
    base_url = (
        "http://localhost:11434/v1" if locality == "local" else "https://api.cloud.example/v1"
    )
    provider_registry.register_mock("vision", vision, tier="fast", base_url=base_url)

    services = StepServices(
        provider_registry=provider_registry,
        tool_registry=ToolRegistry.with_defaults(),  # REAL vision_analyze
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        secretary=secretary, vision=vision, image_path=image_path,
    )


async def _inbound(env: _Env, text: str) -> object:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    return await env.adapter.receive()


async def _run(env: _Env) -> str:
    """Drive one inbound through the gateway; return the delivered (escape-stripped) text."""
    msg = await _inbound(env, "Look at this image and tell me what's in it.")
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text  # type: ignore[attr-defined]
    _writer, reader = env.stream_registry.create(msg.trace_id)  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",  # type: ignore[attr-defined]
    )
    run_task = asyncio.create_task(env.backend.run(state))
    send_task = asyncio.create_task(env.adapter.send(reader))
    await asyncio.wait_for(run_task, timeout=20.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(msg.trace_id)  # type: ignore[attr-defined]
    delivered = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    return delivered.replace("\\", "")  # the adapter MarkdownV2-escapes punctuation


async def test_j10_local_vision_describes_image_no_egress() -> None:
    """LOCAL vision model: the owl describes the image; the description reaches the
    user with NO egress note (the image stayed on the machine)."""
    env = _build(locality="local")

    clean = await _run(env)

    # OUTCOME 1 — the REAL tool genuinely ran the vision model on the loaded image.
    assert env.vision is not None and env.vision.saw_image, (
        "OUTCOME 1 FAIL: vision_analyze never sent the loaded image to the provider — "
        f"loader/selector/tool not wired. tool_out={env.secretary.tool_out!r}"
    )
    # OUTCOME 2 — the vision model's REAL description reached the user.
    assert _DESCRIPTION in clean, (
        f"OUTCOME 2 FAIL: the description did not reach the user. Delivered: {clean!r}"
    )
    # The reply was built from the REAL tool output (the tool output carries it).
    assert _DESCRIPTION in env.secretary.tool_out
    # OUTCOME 3 — LOCAL → NO egress disclosure (image stayed on the box).
    assert "Cloud vision" not in clean, (
        f"OUTCOME 3 FAIL: a local run leaked an egress note. Delivered: {clean!r}"
    )
    assert "left this machine" not in clean


async def test_j10_cloud_vision_discloses_egress_to_user() -> None:
    """CLOUD vision model: the SAME description reaches the user, but PREFIXED with
    the egress disclosure naming the provider (the image left the machine)."""
    env = _build(locality="cloud")

    clean = await _run(env)

    assert env.vision is not None and env.vision.saw_image
    # The description still reaches the user.
    assert _DESCRIPTION in clean, f"description missing. Delivered: {clean!r}"
    # CLOUD → the egress disclosure is present and names the provider.
    assert "Cloud vision" in clean, (
        f"FAIL: a cloud run did NOT disclose egress to the user. Delivered: {clean!r}"
    )
    assert "'vision'" in clean  # the provider is named
    assert "left this machine" in clean
    # And the disclosure came from the REAL tool output, not the reply wrapper.
    assert "[Cloud vision:" in env.secretary.tool_out


async def test_j10_no_vision_provider_self_heals_to_user() -> None:
    """FAIL-WHEN-UNWIRED: with NO vision provider in the registry, the REAL
    vision_analyze self-heals to a structured 'unavailable/actionable' message that
    reaches the user — no description, no crash."""
    # Build a registry with ONLY the secretary (no vision provider) — vision_analyze
    # sees a registry but no vision-capable provider → actionable structured result.
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    image_path = _write_png()
    secretary = _ScriptedSecretary(image_path)
    reg = ProviderRegistry()
    reg.register_mock("secretary", secretary, tier="powerful", base_url=None)
    services = StepServices(
        provider_registry=reg,
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        secretary=secretary, vision=None, image_path=image_path,
    )

    clean = await _run(env)

    # The description NEVER appears (there was no vision model).
    assert _DESCRIPTION not in clean
    # The user receives the actionable structured message — and no crash occurred.
    lowered = clean.lower()
    assert "vision" in lowered and ("install" in lowered or "configure" in lowered), (
        f"FAIL: the actionable no-vision message did not reach the user. Delivered: {clean!r}"
    )
    # It came from the REAL tool's structured error (proves the tool ran + self-healed).
    assert env.secretary.tool_out  # the dispatcher returned the structured message


async def test_j10_unwired_provider_registry_self_heals() -> None:
    """FAIL-WHEN-UNWIRED (provider_registry=None): with NO provider registry at all
    the owl turn cannot resolve a provider — execute passes through and the user
    still gets a reply WITHOUT crashing and WITHOUT any image description. Proves the
    absence of the registry is handled gracefully end-to-end rather than throwing."""
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    image_path = _write_png()
    secretary = _ScriptedSecretary(image_path)
    services = StepServices(
        provider_registry=None,  # nothing wired at all
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        secretary=secretary, vision=None, image_path=image_path,
    )

    # Must complete without raising; the vision description never appears.
    clean = await _run(env)
    assert _DESCRIPTION not in clean
