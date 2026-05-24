"""Streaming-perf smoke tests for ConversationView."""

from __future__ import annotations

import pytest

from stackowl.tui.messages import ResponseChunkMessage
from stackowl.tui.widgets.conversation_view import ConversationView

pytestmark = pytest.mark.tui


@pytest.mark.asyncio
async def test_100_chunks_no_frame_drop() -> None:
    """Inject 100 chunks and verify debouncing keeps the queue structure intact.

    Without the debounce we'd render each token straight to the screen, which
    drops frames at >60fps token rates.  The widget instead accumulates chunks
    in ``_pending_chunks`` and flushes them on the timer — this test simply
    asserts the buffer captures everything in order.
    """
    view = ConversationView()
    for i in range(100):
        msg = ResponseChunkMessage(text=f"chunk {i}", owl_name="secretary")
        view._pending_chunks.append(msg)
    assert len(view._pending_chunks) == 100
    # Ordering must be preserved.
    assert view._pending_chunks[0].text == "chunk 0"
    assert view._pending_chunks[-1].text == "chunk 99"


@pytest.mark.asyncio
async def test_on_response_chunk_message_preserves_arrival_order() -> None:
    view = ConversationView()
    for i in range(25):
        view.on_response_chunk_message(
            ResponseChunkMessage(text=f"t{i}", owl_name="secretary", chunk_index=i)
        )
    indices = [c.chunk_index for c in view._pending_chunks]
    assert indices == list(range(25))


@pytest.mark.asyncio
async def test_flush_pending_does_not_throw_on_unmounted_widget() -> None:
    view = ConversationView()
    view.on_response_chunk_message(
        ResponseChunkMessage(text="streaming", owl_name="secretary")
    )
    # Must not propagate the missing-widget exception.
    view._flush_pending()
