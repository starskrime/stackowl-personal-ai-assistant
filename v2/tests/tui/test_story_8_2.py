"""Story 8.2 — ResponseChunkMessage, FactCitation, ConversationView."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.coordinator import UIStateCoordinator
from stackowl.tui.messages import FactCitation, ResponseChunkMessage
from stackowl.tui.widgets.conversation_helpers import (
    DEFAULT_FLUSH_INTERVAL_SEC,
    PUSHBACK_INDICATOR,
)
from stackowl.tui.widgets.conversation_view import ConversationView

pytestmark = pytest.mark.tui


_TCSS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "widgets"
    / "conversation_view.tcss"
)


class _FakeApp:
    """Minimal Textual.App stand-in capturing posted messages."""

    def __init__(self) -> None:
        self.posted: list[Any] = []

    def call_from_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def post_message(self, message: Any) -> None:
        self.posted.append(message)


# ---------------------------------------------------------------------------
# A. Message + citation dataclasses
# ---------------------------------------------------------------------------


def test_response_chunk_message_has_required_fields() -> None:
    msg = ResponseChunkMessage(
        text="hello",
        owl_name="secretary",
        citations=(FactCitation(fact_id="f1", snippet="s", index=1),),
        is_pushback=False,
        is_synthesis=True,
        chunk_index=7,
        trace_id="abcd1234",
    )
    assert msg.text == "hello"
    assert msg.owl_name == "secretary"
    assert msg.is_synthesis is True
    assert msg.is_pushback is False
    assert msg.chunk_index == 7
    assert msg.trace_id == "abcd1234"
    assert isinstance(msg.citations, tuple)
    assert msg.citations[0].fact_id == "f1"
    # Frozen — must reject reassignment.
    assert dataclasses.is_dataclass(msg)
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.text = "other"  # type: ignore[misc]


def test_fact_citation_has_fact_id_snippet_index() -> None:
    cit = FactCitation(fact_id="f42", snippet="a snippet", index=3)
    assert cit.fact_id == "f42"
    assert cit.snippet == "a snippet"
    assert cit.index == 3
    # NOT frozen — mutability is fine for the inner data record.
    cit.snippet = "rewritten"
    assert cit.snippet == "rewritten"


def test_response_chunk_message_defaults_are_safe() -> None:
    msg = ResponseChunkMessage(text="x", owl_name="o")
    assert msg.citations == ()
    assert msg.is_pushback is False
    assert msg.is_synthesis is False
    assert msg.chunk_index == 0
    assert msg.trace_id == ""


# ---------------------------------------------------------------------------
# B. ConversationView._render_chunk()
# ---------------------------------------------------------------------------


def test_render_chunk_plain_text() -> None:
    view = ConversationView()
    msg = ResponseChunkMessage(text="hello world", owl_name="secretary")
    out = view._render_chunk(msg)
    assert out == "hello world"


def test_render_chunk_synthesis_adds_separator() -> None:
    view = ConversationView()
    msg = ResponseChunkMessage(
        text="final answer", owl_name="parliament", is_synthesis=True
    )
    out = view._render_chunk(msg)
    # Separator glyph (or ASCII fallback) appears in brackets above the text.
    assert "final answer" in out
    assert out.startswith("[")
    assert "\nfinal answer" in out


def test_render_chunk_pushback_adds_indicator() -> None:
    view = ConversationView()
    msg = ResponseChunkMessage(text="not so fast", owl_name="critic", is_pushback=True)
    out = view._render_chunk(msg)
    assert out.startswith(PUSHBACK_INDICATOR)
    assert "not so fast" in out


def test_render_chunk_citations_add_index_markers() -> None:
    view = ConversationView()
    msg = ResponseChunkMessage(
        text="see source",
        owl_name="secretary",
        citations=(FactCitation(fact_id="f1", snippet="s", index=2),),
    )
    out = view._render_chunk(msg)
    assert "see source" in out
    assert "[2]" in out


def test_render_chunk_multiple_citations_all_appear() -> None:
    view = ConversationView()
    msg = ResponseChunkMessage(
        text="claim",
        owl_name="secretary",
        citations=(
            FactCitation(fact_id="f1", snippet="s1", index=1),
            FactCitation(fact_id="f2", snippet="s2", index=2),
            FactCitation(fact_id="f3", snippet="s3", index=7),
        ),
    )
    out = view._render_chunk(msg)
    assert "[1]" in out
    assert "[2]" in out
    assert "[7]" in out


def test_render_chunk_empty_citations_renders_plain() -> None:
    view = ConversationView()
    msg = ResponseChunkMessage(text="plain", owl_name="secretary", citations=())
    out = view._render_chunk(msg)
    assert out == "plain"


# ---------------------------------------------------------------------------
# C. Queue / flush behaviour
# ---------------------------------------------------------------------------


def test_on_response_chunk_message_queues_chunk() -> None:
    view = ConversationView()
    msg = ResponseChunkMessage(text="t", owl_name="secretary")
    view.on_response_chunk_message(msg)
    assert len(view._pending_chunks) == 1
    assert view._pending_chunks[0] is msg


def test_flush_pending_clears_after_flush_failure_path() -> None:
    """When no RichLog is mounted, _flush_pending must early-return and
    leave queued chunks in place (the next flush will retry once mounted)."""
    view = ConversationView()
    view._pending_chunks.append(ResponseChunkMessage(text="t", owl_name="o"))
    # No app context — query_one will raise; helper must catch and log.
    view._flush_pending()
    # The widget was never mounted, so chunks remain pending (not silently lost).
    assert len(view._pending_chunks) == 1


def test_flush_pending_noop_when_empty() -> None:
    view = ConversationView()
    # No chunks queued — must not raise even though no RichLog is mounted.
    view._flush_pending()
    assert view._pending_chunks == []


def test_auto_scroll_true_on_construction() -> None:
    view = ConversationView()
    assert view._auto_scroll is True


# ---------------------------------------------------------------------------
# D. UIStateCoordinator integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_dispatch_response_chunk() -> None:
    bus = EventBus()
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=bus)  # type: ignore[arg-type]
    await coord._dispatch(
        "response_chunk",
        {
            "text": "tok",
            "owl_name": "secretary",
            "chunk_index": 4,
            "trace_id": "trace-xyz",
            "citations": [
                {"fact_id": "f1", "snippet": "snip", "index": 1},
            ],
            "is_pushback": False,
            "is_synthesis": False,
        },
    )
    assert len(app.posted) == 1
    msg = app.posted[0]
    assert isinstance(msg, ResponseChunkMessage)
    assert msg.text == "tok"
    assert msg.owl_name == "secretary"
    assert msg.chunk_index == 4
    assert msg.trace_id == "trace-xyz"
    assert len(msg.citations) == 1
    assert msg.citations[0].fact_id == "f1"
    assert msg.citations[0].index == 1


# ---------------------------------------------------------------------------
# E. TCSS asset
# ---------------------------------------------------------------------------


def test_conversation_view_tcss_exists() -> None:
    assert _TCSS_PATH.is_file(), f"missing tcss at {_TCSS_PATH}"


def test_conversation_view_tcss_uses_tokens_only() -> None:
    body = _TCSS_PATH.read_text(encoding="utf-8")
    # Strip comments so example-laden docs don't trip the check.
    stripped = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stripped), (
        f"hex literal found in {_TCSS_PATH}"
    )
    assert not re.search(r"rgba?\s*\(", stripped), (
        f"rgb()/rgba() literal found in {_TCSS_PATH}"
    )
    # Must reference the expected design tokens.
    assert "$color-bg" in stripped
    assert "$color-border" in stripped


# ---------------------------------------------------------------------------
# F. BINDINGS / DEFAULT_CSS introspection
# ---------------------------------------------------------------------------


def _binding_keys(view: ConversationView) -> set[str]:
    keys: set[str] = set()
    for b in view.BINDINGS:
        if isinstance(b, tuple):
            keys.add(b[0])
        else:
            keys.add(getattr(b, "key", ""))
    return keys


def test_conversation_view_has_end_binding() -> None:
    view = ConversationView()
    assert "end" in _binding_keys(view)


def test_conversation_view_has_home_binding() -> None:
    view = ConversationView()
    assert "home" in _binding_keys(view)


def test_default_css_references_design_tokens() -> None:
    css = ConversationView.DEFAULT_CSS
    assert "$color-border" in css
    assert "$color-bg" in css


def test_flush_interval_default_is_60fps_window() -> None:
    # Constant must stay aligned with widget docstring guarantees.
    assert DEFAULT_FLUSH_INTERVAL_SEC == pytest.approx(0.016)
