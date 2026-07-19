import httpx
import pytest

from stackowl.providers import model_window as mw


@pytest.fixture(autouse=True)
def _clear_cache():
    mw._WINDOW_CACHE.clear()
    mw._reset_probe_client()
    yield
    mw._WINDOW_CACHE.clear()
    mw._reset_probe_client()


def test_config_override_wins_and_converts_chars_to_tokens():
    w = mw.window_from_config(context_chars=40000)
    assert w == 10000  # 40000 // 4


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
        async def post(self, url, json):
            assert url.endswith("/api/show")
            return _Resp()

    monkeypatch.setattr(mw, "_new_probe_client", lambda: _Client())
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="qwen3.5:9b", context_chars=None, protocol="openai",
    )
    # The model's REAL probed window is honored (32768) — dynamic from the model,
    # no platform cap.
    assert w == 32768


async def test_resolve_probe_failure_falls_back(monkeypatch):
    class _Boom:
        async def post(self, *a, **k): raise httpx.ConnectError("down")

    monkeypatch.setattr(mw, "_new_probe_client", lambda: _Boom())
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="m", context_chars=None, protocol="openai",
    )
    assert w == mw.DEFAULT_WINDOW_FALLBACK  # 262144 (256K), raised 2026-07-18


async def test_cloud_default_for_anthropic_without_probe():
    w = await mw.resolve_window(
        provider_name="claude", base_url=None,
        model="claude-x", context_chars=None, protocol="anthropic",
    )
    assert w == mw._CLOUD_DEFAULT


def test_cached_window_returns_none_when_absent():
    assert mw.cached_window("never", "seen") is None


# Live incident (2026-07-18): a custom OpenAI-compatible gateway (LiteLLM in
# front of a vLLM-style backend) had no context_chars configured and isn't
# ollama, so it fell to DEFAULT_WINDOW_FALLBACK (8192) even though its REAL
# window was 262144 (32x more) — needlessly triggering lean-mode degradation.
# These lock in the active max_tokens-probe fix.


async def test_resolve_probes_openai_compatible_max_total_tokens_shape(monkeypatch):
    """LiteLLM's actual observed error shape: "...max_model_len=max_total_tokens=262144..."."""
    class _Resp:
        text = (
            '{"error":{"message":"litellm.BadRequestError: OpenAIException - '
            'max_tokens=999999999cannot be greater than '
            'max_model_len=max_total_tokens=262144. Please request fewer output '
            'tokens.","type":null,"param":null,"code":"400"}}'
        )

    class _Client:
        async def post(self, url, json, headers):
            assert url == "http://gw.example.com:4000/v1/chat/completions"
            assert json["max_tokens"] == mw._PROBE_MAX_TOKENS
            assert headers == {"Authorization": "Bearer sk-test"}
            return _Resp()

    monkeypatch.setattr(mw, "_new_probe_client", lambda: _Client())
    w = await mw.resolve_window(
        provider_name="NeraAiRaw", base_url="http://gw.example.com:4000/v1",
        model="neraai-v1-raw", context_chars=None, protocol="openai",
        api_key="sk-test",
    )
    assert w == 262144


async def test_resolve_probes_openai_compatible_maximum_context_length_shape(monkeypatch):
    """A second observed phrasing: "This model's maximum context length is N tokens"."""
    class _Resp:
        text = (
            "This model's maximum context length is 131072 tokens. However, "
            "you requested more."
        )

    class _Client:
        async def post(self, url, json, headers):
            return _Resp()

    monkeypatch.setattr(mw, "_new_probe_client", lambda: _Client())
    w = await mw.resolve_window(
        provider_name="other", base_url="http://gw2.example.com/v1",
        model="m", context_chars=None, protocol="openai", api_key=None,
    )
    assert w == 131072


async def test_resolve_openai_compatible_probe_no_limit_in_response_falls_back(monkeypatch):
    class _Resp:
        text = '{"choices": [{"message": {"content": "hi there"}}]}'

    class _Client:
        async def post(self, url, json, headers):
            return _Resp()

    monkeypatch.setattr(mw, "_new_probe_client", lambda: _Client())
    w = await mw.resolve_window(
        provider_name="other2", base_url="http://gw3.example.com/v1",
        model="m", context_chars=None, protocol="openai",
    )
    assert w == mw.DEFAULT_WINDOW_FALLBACK


async def test_resolve_openai_compatible_probe_request_failure_falls_back(monkeypatch):
    class _Boom:
        async def post(self, *a, **k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(mw, "_new_probe_client", lambda: _Boom())
    w = await mw.resolve_window(
        provider_name="other3", base_url="http://gw4.example.com/v1",
        model="m", context_chars=None, protocol="openai",
    )
    assert w == mw.DEFAULT_WINDOW_FALLBACK


async def test_resolve_still_uses_ollama_probe_not_the_openai_compatible_one(monkeypatch):
    """Ollama must keep going through _probe_ollama (its own richer model_info
    endpoint) — the new openai-compatible probe is only the non-ollama fallback."""
    called = {"ollama": False, "openai_compatible": False}

    async def _fake_ollama(base_url, model):
        called["ollama"] = True
        return 32768

    async def _fake_openai_compatible(base_url, model, api_key):
        called["openai_compatible"] = True
        return 999

    monkeypatch.setattr(mw, "_probe_ollama", _fake_ollama)
    monkeypatch.setattr(mw, "_probe_openai_compatible", _fake_openai_compatible)
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="m", context_chars=None, protocol="openai",
    )
    assert w == 32768
    assert called == {"ollama": True, "openai_compatible": False}
