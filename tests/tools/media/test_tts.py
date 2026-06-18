"""E10-S3 — tts tool: select/synthesize + PATH-not-bytes + egress disclosure.

Network-free / install-free: the tool is driven with an INJECTED selector backed
by fake backends — no pip install, no synthesis library, no HTTP. Asserts the
five contract points:

* a local backend → a PATH under media_dir() surfaced (not bytes), voice/backend
  reported, the file exists, NO egress note;
* a cloud backend → the egress disclosure PREPENDED (text left the box);
* selector local-first (a local backend is preferred over a configured cloud);
* nothing available → an ACTIONABLE structured unavailable (success False, never raises);
* the full text is NEVER logged (only its length).
"""

from __future__ import annotations

import logging

import pytest

from stackowl.config.settings import TtsSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.media.tts.base import TtsAvailability, TtsBackend, TtsResult
from stackowl.media.tts.selector import TtsSelector
from stackowl.paths import StackowlHome
from stackowl.tools.media.tts import TtsTool

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):  # noqa: ANN202
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _FakeBackend(TtsBackend):
    """A backend that writes a real (tiny) file under media_dir and reports locality."""

    def __init__(self, name: str, *, local: bool, available: bool = True, reason: str | None = None) -> None:
        self._name = name
        self._local = local
        self._available = available
        self._reason = reason
        self.synth_called_with_text: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return self._local

    async def is_available(self, voice: str | None = None) -> TtsAvailability:
        return TtsAvailability.ok() if self._available else TtsAvailability.no(self._reason or "down")

    async def synthesize(self, text: str, *, voice: str | None) -> TtsResult | str:
        self.synth_called_with_text = text
        out_dir = StackowlHome.media_dir() / "tts"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self._name}.wav"
        path.write_bytes(b"RIFF....WAVEfake")
        return TtsResult(
            path=str(path), duration_ms=123.0, voice=voice or "default",
            backend=self._name, is_local=self._local,
        )


def _selector(local: TtsBackend, cloud: TtsBackend, *, engine: str = "auto") -> TtsSelector:
    return TtsSelector(TtsSettings(engine=engine), local=local, cloud=cloud)  # type: ignore[arg-type]


async def test_local_returns_path_under_media_dir_no_egress() -> None:
    local = _FakeBackend("piper", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = TtsTool(selector=_selector(local, cloud))
    res = await tool.execute(text="speak this")
    assert res.success is True
    # local-first: the LOCAL backend ran (not cloud).
    assert local.synth_called_with_text == "speak this"
    assert cloud.synth_called_with_text is None
    # the result surfaces a PATH (not bytes) under media_dir, and the file exists.
    assert StackowlHome.media_dir().as_posix() in res.output
    assert "piper.wav" in res.output
    assert "backend=piper" in res.output and "voice=default" in res.output
    assert "duration_ms=123" in res.output
    out_path = StackowlHome.media_dir() / "tts" / "piper.wav"
    assert out_path.exists()
    # LOCAL → NO egress note.
    assert "[Cloud TTS:" not in res.output


async def test_cloud_backend_prepends_egress_disclosure() -> None:
    # local unavailable → auto falls back to cloud → egress disclosed.
    local = _FakeBackend("piper", local=True, available=False, reason="not installed")
    cloud = _FakeBackend("cloud", local=False)
    tool = TtsTool(selector=_selector(local, cloud, engine="auto"))
    res = await tool.execute(text="secret words", voice="alloy")
    assert res.success is True
    assert cloud.synth_called_with_text == "secret words"
    assert res.output.startswith("[Cloud TTS:")
    assert "'cloud'" in res.output  # names the backend
    assert "left this machine" in res.output
    assert "backend=cloud" in res.output  # the real metadata still included


async def test_local_preferred_over_configured_cloud() -> None:
    local = _FakeBackend("piper", local=True, available=True)
    cloud = _FakeBackend("cloud", local=False, available=True)
    tool = TtsTool(selector=_selector(local, cloud, engine="auto"))
    res = await tool.execute(text="hi")
    assert res.success is True
    assert local.synth_called_with_text == "hi"  # local won
    assert cloud.synth_called_with_text is None
    assert "[Cloud TTS:" not in res.output


async def test_no_backend_is_actionable_never_raises() -> None:
    local = _FakeBackend("piper", local=True, available=False, reason="install failed")
    cloud = _FakeBackend("cloud", local=False, available=False, reason="no key")
    tool = TtsTool(selector=_selector(local, cloud, engine="auto"))
    res = await tool.execute(text="hello")  # must NOT raise
    assert res.success is False
    reason = (res.error or "").lower()
    assert "tts unavailable" in reason
    assert "cloud_enabled" in reason  # actionable


async def test_empty_text_is_structured_error() -> None:
    local = _FakeBackend("piper", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = TtsTool(selector=_selector(local, cloud))
    res = await tool.execute(text="   ")
    assert res.success is False
    assert "text" in (res.error or "").lower()


async def test_synthesis_error_degrades(monkeypatch) -> None:
    class _ErrBackend(_FakeBackend):
        async def synthesize(self, text: str, *, voice: str | None) -> TtsResult | str:
            return "local TTS synthesis failed: Boom"

    local = _ErrBackend("piper", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = TtsTool(selector=_selector(local, cloud))
    res = await tool.execute(text="hello")
    assert res.success is False
    assert "synthesis failed" in (res.error or "")


async def test_full_text_never_logged(caplog) -> None:
    secret = "this is a very secret sentence that must never appear in logs"
    local = _FakeBackend("piper", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = TtsTool(selector=_selector(local, cloud))
    with caplog.at_level(logging.DEBUG):
        res = await tool.execute(text=secret)
    assert res.success is True
    # The secret text must not appear in ANY captured log record.
    assert secret not in caplog.text


async def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    tool = reg.get("tts")
    assert tool is not None
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.toolset_group == "media"
