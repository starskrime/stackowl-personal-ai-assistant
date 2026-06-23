"""Convert IngressMessage <-> IngressFrame across the gateway/core boundary.

Lives in ``runtime`` (the wiring layer) rather than ``ipc`` so the transport
package stays free of any turn/domain types.
"""

from __future__ import annotations

from stackowl.gateway.scanner import IngressMessage
from stackowl.ipc.frames import IngressFrame


def ingress_to_frame(msg: IngressMessage) -> IngressFrame:
    return IngressFrame(
        text=msg.text,
        session_id=msg.session_id,
        channel=msg.channel,
        trace_id=msg.trace_id,
        chat_id=msg.chat_id,
        is_reply=msg.is_reply,
    )


def frame_to_ingress(frame: IngressFrame) -> IngressMessage:
    return IngressMessage(
        text=frame.text,
        session_id=frame.session_id,
        channel=frame.channel,
        trace_id=frame.trace_id,
        chat_id=frame.chat_id,
        is_reply=frame.is_reply,
    )
