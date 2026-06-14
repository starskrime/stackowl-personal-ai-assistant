"""Task 6 — inject budgeted window as ollama num_ctx (extra_body).

Ollama silently truncates input to its own num_ctx default unless told otherwise.
The provider must spread ``extra_body={"options": {"num_ctx": W}}`` into BOTH
``chat.completions.create(...)`` calls when:
  (a) the base_url is an ollama-family URL (contains ":11434" or "ollama"), AND
  (b) the model's window was already resolved and cached.

Non-ollama providers must NOT receive extra_body.

Tests:
  1. ``_ollama_extra_body`` helper — ollama URL + cached window → correct dict.
  2. ``_ollama_extra_body`` helper — non-ollama URL → empty dict.
  3. ``_ollama_extra_body`` helper — ollama URL, no cached window → empty dict.
  4. Integration: main-loop ``create()`` receives ``extra_body`` for ollama.
  5. Integration: wrapup ``create()`` also receives ``extra_body`` for ollama.
  6. Integration: non-ollama provider → ``create()`` receives NO ``extra_body``.

Uses the fake-client pattern from test_phaseF_max_out / test_openai_enforce_veto.
"""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers import model_window as mw
from stackowl.providers.openai_provider import OpenAIProvider

# ---------------------------------------------------------------------------
# Shared fake-client helpers (mirrors test_phaseF_max_out pattern exactly)
# ---------------------------------------------------------------------------


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(
        self,
        content: str | None,
        tool_calls: list[_FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "qwen3.5:9b"
        self.usage = None


class _FinalAnswerCompletions:
    """Returns a final text answer immediately (no tool calls) so complete_with_tools
    exits in 1 iteration.  Captures all kwargs passed to each create() call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []  # one entry per create() call

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(dict(kwargs))
        return _FakeResponse(_FakeMessage(content="DONE", tool_calls=None))


class _ToolThenFinalCompletions:
    """First call: returns a tool call.  Second call: returns a final text answer.
    This exercises BOTH the main-loop create (with tools=) AND the iteration after
    the tool result is appended — letting us confirm extra_body on the main-loop path.
    We don't want a wrap-up (max-out); we want a clean 2-step exit."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self._n += 1
        self.calls.append(dict(kwargs))
        if self._n == 1:
            tc = _FakeToolCall(id="c1", name="web_search", arguments='{"query":"q1"}')
            return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
        return _FakeResponse(_FakeMessage(content="FINAL", tool_calls=None))


class _MaxOutCompletions:
    """Always returns a tool call so the loop hits max_iterations, triggering the
    wrapup create() call (no tools=).  After max_iterations the wrapup fires."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self._n += 1
        self.calls.append(dict(kwargs))
        has_tools = "tools" in kwargs and kwargs["tools"] is not None
        if has_tools:
            tc = _FakeToolCall(
                id=f"c{self._n}", name="web_search",
                arguments=f'{{"query":"q{self._n}"}}',
            )
            return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
        # wrapup call (no tools)
        return _FakeResponse(_FakeMessage(content="WRAPUP", tool_calls=None))


class _FakeChat:
    def __init__(self, completions: Any) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: Any) -> None:
        self.chat = _FakeChat(completions)


# ---------------------------------------------------------------------------
# Helpers to build providers
# ---------------------------------------------------------------------------

_OLLAMA_BASE_URL = "http://x:11434/v1"
_OPENAI_BASE_URL = "https://api.openai.com/v1"
_MODEL = "qwen3.5:9b"
_PROVIDER_NAME = "ollama"
_CACHED_WINDOW = 12000

_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return "some observation"


def _make_provider(base_url: str, provider_name: str = _PROVIDER_NAME) -> OpenAIProvider:
    config = ProviderConfig(
        name=provider_name,
        protocol="openai",
        base_url=base_url,
        default_model=_MODEL,
        tier="local",
    )
    provider = OpenAIProvider(config, api_key="")
    return provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_window_cache() -> Any:
    mw._WINDOW_CACHE.clear()
    yield
    mw._WINDOW_CACHE.clear()


# ---------------------------------------------------------------------------
# 1–3: Unit tests for _ollama_extra_body helper
# ---------------------------------------------------------------------------


def test_ollama_extra_body_returns_num_ctx_when_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama URL + pre-seeded cache → returns extra_body dict with correct window."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    mw._WINDOW_CACHE[(_PROVIDER_NAME, _MODEL)] = _CACHED_WINDOW
    provider = _make_provider(_OLLAMA_BASE_URL)
    result = provider._ollama_extra_body(_MODEL)
    assert result == {"extra_body": {"options": {"num_ctx": _CACHED_WINDOW}}}


def test_ollama_extra_body_non_ollama_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-ollama base_url → empty dict, even if a window is cached."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    mw._WINDOW_CACHE[(_PROVIDER_NAME, _MODEL)] = _CACHED_WINDOW
    provider = _make_provider(_OPENAI_BASE_URL)
    result = provider._ollama_extra_body(_MODEL)
    assert result == {}


def test_ollama_extra_body_no_cached_window_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama URL but no cached window → empty dict (don't send a spurious 0)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # Cache is clear (autouse fixture)
    provider = _make_provider(_OLLAMA_BASE_URL)
    result = provider._ollama_extra_body(_MODEL)
    assert result == {}


# ---------------------------------------------------------------------------
# 4: Integration — main-loop create() receives extra_body for ollama
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_loop_create_receives_extra_body_for_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """complete_with_tools main-loop create() call is spread with extra_body when
    the base_url is ollama and the window is pre-seeded in the cache."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    mw._WINDOW_CACHE[(_PROVIDER_NAME, _MODEL)] = _CACHED_WINDOW

    completions = _FinalAnswerCompletions()
    provider = _make_provider(_OLLAMA_BASE_URL)
    provider._client = _FakeClient(completions)  # type: ignore[assignment]

    text, _calls = await provider.complete_with_tools(
        user_text="hello",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text == "DONE"
    assert len(completions.calls) == 1, "expected exactly 1 create() call"
    call_kwargs = completions.calls[0]
    assert "extra_body" in call_kwargs, "extra_body must be passed to create() for ollama"
    assert call_kwargs["extra_body"] == {"options": {"num_ctx": _CACHED_WINDOW}}


# ---------------------------------------------------------------------------
# 5: Integration — wrapup create() also receives extra_body for ollama
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrapup_create_receives_extra_body_for_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapup create() (no tools=, fired at max-out) also carries extra_body."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    mw._WINDOW_CACHE[(_PROVIDER_NAME, _MODEL)] = _CACHED_WINDOW

    completions = _MaxOutCompletions()
    provider = _make_provider(_OLLAMA_BASE_URL)
    # Use max_iterations=1 so we hit max-out after one tool-call iteration,
    # keeping the test fast while still triggering the wrapup path.
    provider._config = ProviderConfig(
        name=_PROVIDER_NAME,
        protocol="openai",
        base_url=_OLLAMA_BASE_URL,
        default_model=_MODEL,
        tier="local",
        tool_max_iterations=1,
    )
    provider._client = _FakeClient(completions)  # type: ignore[assignment]

    text, _calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
        max_iterations=1,
    )

    assert text == "WRAPUP"
    # The last call is the wrapup (no tools=); confirm extra_body was present.
    wrapup_call = completions.calls[-1]
    assert "tools" not in wrapup_call or wrapup_call.get("tools") is None, (
        "wrapup call must not carry tools="
    )
    assert "extra_body" in wrapup_call, "wrapup create() must also carry extra_body for ollama"
    assert wrapup_call["extra_body"] == {"options": {"num_ctx": _CACHED_WINDOW}}


# ---------------------------------------------------------------------------
# 6: Integration — non-ollama provider → no extra_body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_ollama_create_does_not_receive_extra_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-ollama base_url: create() must NOT receive extra_body at all."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # Seed a window anyway to prove the URL guard — not the cache — is the gate.
    mw._WINDOW_CACHE[("openai", _MODEL)] = _CACHED_WINDOW

    completions = _FinalAnswerCompletions()
    provider = _make_provider(_OPENAI_BASE_URL, provider_name="openai")
    provider._client = _FakeClient(completions)  # type: ignore[assignment]

    text, _calls = await provider.complete_with_tools(
        user_text="hello",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text == "DONE"
    assert len(completions.calls) == 1
    call_kwargs = completions.calls[0]
    assert "extra_body" not in call_kwargs, (
        "extra_body must NOT be passed to create() for non-ollama providers"
    )
