"""Newline-delimited JSON codec for IPC frames.

``model_dump_json()`` emits compact JSON with no literal newlines (string fields
with ``\\n`` are escaped as ``\\\\n``), so a single ``\\n`` is an unambiguous
frame delimiter. Decoding validates against the discriminated ``Frame`` union, so
the concrete frame type is recovered from the ``type`` field.
"""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from stackowl.ipc.frames import Frame

_ADAPTER: TypeAdapter[Frame] = TypeAdapter(Frame)


class FrameDecodeError(ValueError):
    """A wire line could not be decoded into a known frame."""


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
        raise FrameDecodeError(f"undecodable frame: {exc}") from exc
