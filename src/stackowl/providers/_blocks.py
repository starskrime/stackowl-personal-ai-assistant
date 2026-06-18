"""Per-provider serialization of image/document blocks into native wire shapes.

E10-S1 vision substrate. A ``Message`` carries optional ``documents`` — a tuple
of :class:`DocumentBlock`. A block is discriminated by ``media_type``:

* ``image/*``  → an IMAGE content block (vision input)
* anything else (e.g. ``application/pdf``) → a DOCUMENT content block (pdf Mode B)

We reuse the single ``DocumentBlock`` type for both rather than adding a parallel
``ImageBlock``: the carrier is identical (raw ``bytes`` + MIME), only the per-API
serialization differs, and that difference lives here in ONE place per protocol.

Each builder returns the provider-native shape for a user turn that mixes text +
blocks. The three native shapes:

* Anthropic image  → ``{"type":"image","source":{"type":"base64",...}}``
* OpenAI image      → ``{"type":"image_url","image_url":{"url":"data:..."}}``
* Gemini image      → ``{"inline_data":{"mime_type":...,"data":<base64>}}``

The OpenAI ``image_url`` data-URL form also covers a LOCAL Ollama-vision model
(Ollama's OpenAI-compatible endpoint accepts data-URL images), so the one OpenAI
builder serves both cloud OpenAI and self-hosted llava/llama-vision.

Sensitive-data rule: callers log block SIZE + mime, never the bytes (B5).
"""

from __future__ import annotations

import base64
from typing import Any

from stackowl.providers.base import DocumentBlock, Message

__all__ = [
    "anthropic_user_content",
    "gemini_user_parts",
    "message_has_blocks",
    "openai_user_content",
]


def message_has_blocks(message: Message) -> bool:
    """True iff this user message carries any attached image/document block."""
    return bool(message.documents)


def _is_image(block: DocumentBlock) -> bool:
    # Require a non-empty subtype after ``image/`` so a malformed MIME (bare
    # ``image/``) does not mis-route a document as an image block.
    prefix = "image/"
    media = block.media_type.lower()
    return media.startswith(prefix) and len(media) > len(prefix)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --------------------------------------------------------------------------- #
# Anthropic — content is a list of blocks; image source is base64.
# --------------------------------------------------------------------------- #
def anthropic_user_content(message: Message) -> list[dict[str, Any]]:
    """Build Anthropic ``content`` blocks (text + image/document) for one turn.

    Image  → ``{"type":"image","source":{"type":"base64","media_type":..,"data":..}}``
    PDF/doc → ``{"type":"document","source":{"type":"base64",..}}`` (document model).
    """
    blocks: list[dict[str, Any]] = []
    for doc in message.documents:
        kind = "image" if _is_image(doc) else "document"
        blocks.append(
            {
                "type": kind,
                "source": {
                    "type": "base64",
                    "media_type": doc.media_type,
                    "data": _b64(doc.data),
                },
            }
        )
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    return blocks


# --------------------------------------------------------------------------- #
# OpenAI (+ Ollama-vision) — content is a list of parts; image is a data: URL.
# --------------------------------------------------------------------------- #
def openai_user_content(message: Message) -> list[dict[str, Any]]:
    """Build OpenAI/Ollama ``content`` parts (text + image) for one turn.

    Image → ``{"type":"image_url","image_url":{"url":"data:<mime>;base64,<b64>"}}``.
    A non-image document has no portable OpenAI-chat block, so it is sent as a
    descriptive text part (the OpenAI path is the IMAGE path; PDF Mode B routes to
    Anthropic/Gemini document blocks) — never silently dropped.
    """
    parts: list[dict[str, Any]] = []
    if message.content:
        parts.append({"type": "text", "text": message.content})
    for doc in message.documents:
        if _is_image(doc):
            data_url = f"data:{doc.media_type};base64,{_b64(doc.data)}"
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        else:
            parts.append(
                {
                    "type": "text",
                    "text": f"[attached {doc.media_type} document: {doc.filename or 'file'}]",
                }
            )
    return parts


# --------------------------------------------------------------------------- #
# Gemini — parts list; image is inline_data with base64 payload.
# --------------------------------------------------------------------------- #
def gemini_user_parts(message: Message) -> list[dict[str, Any]]:
    """Build Gemini ``parts`` (text + inline image/document) for one turn.

    Image/doc → ``{"inline_data":{"mime_type":<mime>,"data":<base64>}}`` (Gemini
    accepts both images and PDFs as inline_data).
    """
    parts: list[dict[str, Any]] = []
    if message.content:
        parts.append({"text": message.content})
    for doc in message.documents:
        parts.append(
            {"inline_data": {"mime_type": doc.media_type, "data": _b64(doc.data)}}
        )
    return parts
