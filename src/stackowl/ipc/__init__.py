"""Local IPC transport for the gateway<->core two-process split.

A DURABLE gateway process holds client connections (TUI, channels) and a
RESTARTABLE core process runs the agent logic; they talk over a local
unix-domain socket using newline-delimited JSON frames. This package is the
transport only — it knows nothing about turns, the pipeline, or the TUI. The
wiring that maps these frames onto the real ``TurnRegistry`` /
``StreamRegistry`` / ``ClarifyGateway`` lives in ``stackowl.runtime``.
"""

from __future__ import annotations

from stackowl.ipc.client import IpcClient
from stackowl.ipc.codec import FrameDecodeError, decode_frame, encode_frame
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import (
    AckFrame,
    ChunkFrame,
    ClarifyAskFrame,
    ClarifyReplyFrame,
    ConsentRequestFrame,
    ConsentResponseFrame,
    Frame,
    GoodbyeFrame,
    HelloFrame,
    IngressFrame,
    ProgressEventFrame,
    QueryRunningFrame,
    RestartNoticeFrame,
    RunningStateFrame,
    SendTextFrame,
    SteerFrame,
    StopFrame,
)
from stackowl.ipc.server import IpcServer
from stackowl.ipc.stream_bridge import (
    SocketStreamWriter,
    StreamDemux,
    chunk_to_frame,
    frame_to_chunk,
)

__all__ = [
    "AckFrame",
    "ChunkFrame",
    "ClarifyAskFrame",
    "ClarifyReplyFrame",
    "ConsentRequestFrame",
    "ConsentResponseFrame",
    "Frame",
    "FrameConnection",
    "FrameDecodeError",
    "GoodbyeFrame",
    "HelloFrame",
    "IngressFrame",
    "IpcClient",
    "IpcServer",
    "ProgressEventFrame",
    "QueryRunningFrame",
    "RestartNoticeFrame",
    "RunningStateFrame",
    "SendTextFrame",
    "SocketStreamWriter",
    "SteerFrame",
    "StopFrame",
    "StreamDemux",
    "chunk_to_frame",
    "decode_frame",
    "encode_frame",
    "frame_to_chunk",
]
