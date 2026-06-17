"""PROV-5 (F129) — the ollama window probe reuses ONE pooled httpx client.

Each distinct (provider, model) probe used to open + tear down a fresh
``httpx.AsyncClient`` (TLS/conn-pool setup each time). A module-level shared
client is created once and reused; behavior (the resolved window) is identical.
"""

from __future__ import annotations

import pytest

from stackowl.providers import model_window as mw


@pytest.fixture(autouse=True)
def _clear_cache():
    mw._WINDOW_CACHE.clear()
    yield
    mw._WINDOW_CACHE.clear()


@pytest.mark.asyncio
async def test_distinct_model_probes_share_one_pooled_client(monkeypatch) -> None:
    posts: list[str] = []
    created: list[object] = []

    class _Resp:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"model_info": {"x.context_length": 8192}}

    class _Client:
        async def post(self, url, json):  # noqa: ANN001
            posts.append(url)
            return _Resp()

    def _factory():
        client = _Client()
        created.append(client)
        return client

    # The probe must obtain its client through the pooled getter, not construct
    # a fresh httpx.AsyncClient per call.
    monkeypatch.setattr(mw, "_new_probe_client", _factory)
    mw._reset_probe_client()

    w1 = await mw.resolve_window(
        provider_name="ollama", base_url="http://h:11434/v1",
        model="model-a", context_chars=None, protocol="openai",
    )
    w2 = await mw.resolve_window(
        provider_name="ollama", base_url="http://h:11434/v1",
        model="model-b", context_chars=None, protocol="openai",
    )

    assert w1 == 8192
    assert w2 == 8192
    assert len(posts) == 2, "both distinct models were probed"
    assert len(created) == 1, "the pooled client was created exactly once and reused"

    mw._reset_probe_client()
