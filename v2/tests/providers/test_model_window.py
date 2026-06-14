import httpx
import pytest

from stackowl.providers import model_window as mw


@pytest.fixture(autouse=True)
def _clear_cache():
    mw._WINDOW_CACHE.clear()
    yield
    mw._WINDOW_CACHE.clear()


def test_config_override_wins_and_converts_chars_to_tokens():
    w = mw.window_from_config(context_chars=40000)
    assert w == 10000  # 40000 // 4


def test_clamp_to_ceiling():
    assert mw._clamp(999_999) == mw.WINDOW_CEILING_DEFAULT  # 16384
    assert mw._clamp(4096) == 4096


async def test_resolve_uses_config_override_without_probing():
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="m", context_chars=40000, protocol="openai",
    )
    assert w == 10000
    assert mw.cached_window("ollama", "m") == 10000


async def test_resolve_probes_ollama_api_show(monkeypatch):
    class _Resp:
        def raise_for_status(self): ...
        def json(self):
            return {"model_info": {"qwen3.qwen.context_length": 32768}}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json):
            assert url.endswith("/api/show")
            return _Resp()

    monkeypatch.setattr(mw.httpx, "AsyncClient", _Client)
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="qwen3.5:9b", context_chars=None, protocol="openai",
    )
    assert w == mw.WINDOW_CEILING_DEFAULT  # 32768 clamped to 16384


async def test_resolve_probe_failure_falls_back(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise httpx.ConnectError("down")

    monkeypatch.setattr(mw.httpx, "AsyncClient", _Boom)
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="m", context_chars=None, protocol="openai",
    )
    assert w == mw.DEFAULT_WINDOW_FALLBACK  # 8192


async def test_cloud_default_for_anthropic_without_probe():
    w = await mw.resolve_window(
        provider_name="claude", base_url=None,
        model="claude-x", context_chars=None, protocol="anthropic",
    )
    assert w == mw.WINDOW_CEILING_DEFAULT


def test_cached_window_returns_none_when_absent():
    assert mw.cached_window("never", "seen") is None
