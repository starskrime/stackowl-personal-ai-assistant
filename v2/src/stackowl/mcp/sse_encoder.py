"""McpSseEncoder — formats MCP events as Server-Sent Events with field-1-first JSON.

The field-1-first invariant: the ``content`` key must appear first in every
SSE JSON payload so streaming clients can start rendering immediately.
A CI test in tests/mcp/test_sse_encoder.py enforces this on every schema.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict

from pydantic import BaseModel

log = logging.getLogger("stackowl.mcp")


class McpSseEncoder:
    """Encodes MCP events into Server-Sent Event (SSE) wire format."""

    def encode_event(
        self,
        event_type: str,
        data: str,
        id: str | None = None,
    ) -> str:
        """Return an SSE string for the given event.

        Field order per spec: ``id`` (if present), ``event``, ``data``,
        terminated by a blank line (``\\n\\n``).
        """
        log.debug(
            "sse_encoder.encode_event: entry",
            extra={"_fields": {"event_type": event_type, "data_len": len(data), "has_id": id is not None}},
        )
        lines: list[str] = []
        if id is not None:
            lines.append(f"id: {id}")
        lines.append(f"event: {event_type}")
        lines.append(f"data: {data}")
        result = "\n".join(lines) + "\n\n"
        log.debug(
            "sse_encoder.encode_event: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result

    def encode_message(self, payload: BaseModel) -> str:
        """Serialize a Pydantic model to an SSE ``message`` event.

        Guarantees field-1-first: ``content`` is always the first key in the
        JSON object, regardless of the model's field declaration order.
        """
        log.debug(
            "sse_encoder.encode_message: entry",
            extra={"_fields": {"schema": type(payload).__name__}},
        )
        raw = payload.model_dump()

        # Enforce field-1-first: move "content" to position 0.
        ordered: OrderedDict[str, object] = OrderedDict()
        if "content" in raw:
            ordered["content"] = raw["content"]
        for key, value in raw.items():
            if key != "content":
                ordered[key] = value

        data = json.dumps(ordered, ensure_ascii=False)
        result = self.encode_event("message", data)
        log.debug(
            "sse_encoder.encode_message: exit",
            extra={"_fields": {"data_len": len(data)}},
        )
        return result
