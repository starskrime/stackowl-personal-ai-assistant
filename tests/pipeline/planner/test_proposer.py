"""E2-S3 — ToolProposer: LLM picks tools, validated by EXACT membership."""

from __future__ import annotations

from stackowl.pipeline.planner.proposer import ToolProposer
from stackowl.providers.base import CompletionResult


class _FakeProvider:
    def __init__(self, content):  # str | Exception
        self._content = content
        self.seen_model = None

    async def complete(self, messages, model="", **kw):
        self.seen_model = model
        if isinstance(self._content, Exception):
            raise self._content
        return CompletionResult(
            content=self._content,
            input_tokens=0,
            output_tokens=0,
            model="fake",
            provider_name="fake",
            duration_ms=0.0,
        )


class _FakeRegistry:
    def __init__(self, provider, model=""):
        self._p = provider
        self._model = model

    def get_with_cascade(self, tier):
        return self._p, self._model


CATALOG = [("note_search", "Search notes"), ("summarize_text", "Summarize"), ("shell", "Run shell")]


async def test_parses_json_and_validates_exact() -> None:
    p = ToolProposer(_FakeRegistry(_FakeProvider('{"tools": ["note_search", "summarize_text", "made_up"]}')))
    got = await p.propose("summarize my notes", CATALOG)
    assert got == frozenset({"note_search", "summarize_text"})


async def test_hallucination_not_fuzzy_matched() -> None:
    p = ToolProposer(_FakeRegistry(_FakeProvider('{"tools": ["shel", "note_serch"]}')))
    got = await p.propose("x", CATALOG)
    assert got == frozenset()


async def test_provider_error_returns_empty() -> None:
    p = ToolProposer(_FakeRegistry(_FakeProvider(RuntimeError("boom"))))
    assert await p.propose("x", CATALOG) == frozenset()


async def test_no_registry_returns_empty() -> None:
    assert await ToolProposer(None).propose("x", CATALOG) == frozenset()


async def test_empty_catalog_returns_empty() -> None:
    p = ToolProposer(_FakeRegistry(_FakeProvider('{"tools": ["anything"]}')))
    assert await p.propose("x", []) == frozenset()


# ---------------------------------------------------------------------------
# Task 14 — propose() threads the "fast"-tier RESOLVED model into
# provider.complete(), instead of hardcoding model="".
# ---------------------------------------------------------------------------


async def test_propose_threads_resolved_fast_model_to_complete() -> None:
    """``propose()`` resolves the "fast"-tier provider+model via
    ``get_with_cascade("fast")`` and forwards the RESOLVED model into
    ``provider.complete()``.

    Genuinely discriminating: if ``propose()`` still called the old
    ``get_with_cascade`` (dropping the model) or hardcoded ``model=""``,
    ``seen_model`` would stay "" instead of the sentinel resolved model.
    """
    provider = _FakeProvider('{"tools": ["shell"]}')
    p = ToolProposer(_FakeRegistry(provider, model="proposer-fast-resolved"))
    await p.propose("run a command", CATALOG)
    assert provider.seen_model == "proposer-fast-resolved", (
        f"expected the resolved fast-tier model to reach complete(); got {provider.seen_model!r}"
    )
