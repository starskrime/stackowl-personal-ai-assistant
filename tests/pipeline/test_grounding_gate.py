"""Unit tests for surface_grounding_gate (ADR-T3 / TS5+TS6 — anti-fabrication).

Asserts on MEASURED facts (URLs vs the per-turn retrieval ledger), never prose:
  a) cited URL NOT in any retrieval result → stripped
  b) cited URL that WAS in a web_search result → kept unchanged
  c) external URLs but no retrieval ran → floored as unverified
  d) no URLs → unchanged (back-compat)
  e) a URL the user pasted in their own message → not stripped
"""

from __future__ import annotations

import json

import pytest

from stackowl.pipeline.grounding_gate import _FLOOR_TEXT, surface_grounding_gate
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t-ground",
        session_id="s",
        input_text="what's new in AI?",
        channel="cli",
        owl_name="o",
        pipeline_step="execute",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _draft(content: str, *, is_floor: bool = False) -> ResponseChunk:
    return ResponseChunk(
        content=content, is_final=False, chunk_index=0,
        trace_id="t-ground", owl_name="o", is_floor=is_floor,
    )


def _web_search_call(*urls: str) -> ToolCall:
    payload = {
        "success": True,
        "data": {"web": [{"title": "t", "url": u, "description": "d", "position": i}
                         for i, u in enumerate(urls, 1)]},
    }
    return ToolCall(
        tool_name="web_search", args={"query": "ai"},
        result=json.dumps(payload), error=None, duration_ms=1.0,
    )


def _web_fetch_call(url: str, *, error: str | None = None) -> ToolCall:
    return ToolCall(
        tool_name="web_fetch", args={"url": url},
        result="" if error else "# page\nsome content", error=error, duration_ms=1.0,
    )


def _text(state: PipelineState) -> str:
    return "".join(c.content for c in state.responses)


# (a) fabricated URL (retrieval ran but returned a DIFFERENT url) → stripped
@pytest.mark.asyncio
async def test_fabricated_url_stripped() -> None:
    state = _state(
        responses=(_draft(
            "Big news: [GPT-5.6 launched](https://openai.example/gpt56) and lots "
            "more happened across the industry this week, plenty to read."
        ),),
        tool_calls=(_web_search_call("https://realsource.example/article"),),
    )
    result = await surface_grounding_gate(state)
    assert "gpt56" not in _text(result)
    assert "GPT-5.6 launched" in _text(result)  # markdown label preserved
    assert not any(c.is_floor for c in result.responses)


# (b) cited URL that WAS in a web_search result → kept unchanged
@pytest.mark.asyncio
async def test_grounded_url_kept() -> None:
    url = "https://realsource.example/article"
    draft = _draft(f"Per [this report]({url}), models improved a lot this quarter.")
    state = _state(responses=(draft,), tool_calls=(_web_search_call(url),))
    result = await surface_grounding_gate(state)
    assert result.responses == state.responses  # byte-identical
    assert url in _text(result)


# (b2) web_fetch'd URL counts as a fetched source
@pytest.mark.asyncio
async def test_web_fetched_url_kept() -> None:
    url = "https://docs.example/page"
    state = _state(
        responses=(_draft(f"I read {url} and it covers the setup steps in detail."),),
        tool_calls=(_web_fetch_call(url),),
    )
    result = await surface_grounding_gate(state)
    assert result.responses == state.responses
    # a FAILED fetch does not ground the URL
    state2 = _state(
        responses=(_draft(f"I read {url} and it covers the setup steps in detail."),),
        tool_calls=(_web_fetch_call(url, error="HTTP 404"),),
    )
    result2 = await surface_grounding_gate(state2)
    assert url not in _text(result2)  # failed fetch ⇒ URL not grounded ⇒ stripped


# (c) external URLs but no retrieval ran → floored as unverified
@pytest.mark.asyncio
async def test_no_retrieval_floored() -> None:
    state = _state(
        responses=(_draft(
            "Here's the latest AI news: [Fable-5 released](https://fake.example/fable5)."
        ),),
        tool_calls=(),  # NOTHING retrieved
    )
    result = await surface_grounding_gate(state)
    assert result.overclaim_blocked is True
    assert len(result.responses) == 1
    assert result.responses[0].is_floor is True
    assert result.responses[0].content == _FLOOR_TEXT


# (d) no URLs → unchanged (back-compat, byte-identical)
@pytest.mark.asyncio
async def test_no_urls_unchanged() -> None:
    state = _state(responses=(_draft("Sure, I can help you plan that trip."),))
    result = await surface_grounding_gate(state)
    assert result.responses == state.responses
    assert result is state  # truly untouched


# (e) a URL the user themselves pasted → not stripped (echo ≠ fabrication)
@pytest.mark.asyncio
async def test_user_supplied_url_kept() -> None:
    url = "https://userblog.example/post"
    state = _state(
        input_text=f"can you summarize {url} for me",
        responses=(_draft(
            f"Sure — {url} argues that small models can match big ones on narrow tasks."
        ),),
        tool_calls=(),  # no retrieval, but the URL is the user's own
    )
    result = await surface_grounding_gate(state)
    assert result.responses == state.responses
    assert url in _text(result)


# guard: already-floored draft is left untouched
@pytest.mark.asyncio
async def test_already_floored_noop() -> None:
    state = _state(
        responses=(_draft("https://fake.example/x", is_floor=True),),
        tool_calls=(),
    )
    result = await surface_grounding_gate(state)
    assert result is state


# stripping that guts the answer → floor even though retrieval ran
@pytest.mark.asyncio
async def test_gutted_after_strip_floored() -> None:
    state = _state(
        responses=(_draft("See https://fake.example/a and https://fake.example/b"),),
        tool_calls=(_web_search_call("https://real.example/c"),),
    )
    result = await surface_grounding_gate(state)
    assert result.responses[0].is_floor is True
    assert result.responses[0].content == _FLOOR_TEXT
