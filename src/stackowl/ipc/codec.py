"""Newline-delimited JSON codec for IPC frames.

``model_dump_json()`` emits compact JSON with no literal newlines (string fields
with ``\\n`` are escaped as ``\\\\n``), so a single ``\\n`` is an unambiguous
frame delimiter. Decoding validates against the discriminated ``Frame`` union, so
the concrete frame type is recovered from the ``type`` field.
"""

from __future__ import annotations

import json

from pydantic import TypeAdapter, ValidationError

from stackowl.ipc.frames import Frame

_ADAPTER: TypeAdapter[Frame] = TypeAdapter(Frame)


class FrameDecodeError(ValueError):
    """A wire line could not be decoded into a known frame.

    ``frame_type`` is a best-effort, defensive extraction of the line's
    ``type`` field (``None`` when the line isn't even valid JSON, or has no
    ``type`` key) -- Spec 2.3: the connection-level reader logs a WARNING
    naming the type instead of silently skipping the line.
    """

    def __init__(self, message: str, frame_type: str | None = None) -> None:
        super().__init__(message)
        self.frame_type = frame_type


def _best_effort_type(raw: str) -> str | None:
    """Defensively pull ``type`` out of an undecodable line, or ``None``.

    Never raises -- this runs INSIDE an already-failing decode path, so any
    error here (bad JSON, wrong shape) is swallowed and reported as "unknown"
    rather than masking the original :class:`FrameDecodeError`.
    """
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001 — best-effort only, never raises
        return None
    if not isinstance(obj, dict):
        return None
    value = obj.get("type")
    return value if isinstance(value, str) else None


def encode_frame(frame: Frame) -> bytes:
    """Serialise a frame to one newline-terminated JSON line (UTF-8 bytes)."""
    return _ADAPTER.dump_json(frame) + b"\n"


def decode_frame(line: bytes | str) -> Frame:
    """Parse one wire line (with or without trailing newline) into a Frame.

    Raises :class:`FrameDecodeError` on malformed JSON or an unknown ``type``.
    """
    raw = line.decode("utf-8") if isinstance(line, bytes) else line
    raw = raw.strip()
    if not raw:
        raise FrameDecodeError("empty frame line")
    try:
        return _ADAPTER.validate_json(raw)
    except ValidationError as exc:  # unknown type / missing field / bad json
        raise FrameDecodeError(
            f"undecodable frame: {exc}", frame_type=_best_effort_type(raw)
        ) from exc
