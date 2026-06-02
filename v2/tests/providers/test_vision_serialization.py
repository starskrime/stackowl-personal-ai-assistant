"""E10-S1 — provider serialization of image blocks into native wire shapes.

Asserts the request dict each provider would send. The SDK / HTTP layer is faked
(no real network). Covers all three native image shapes + the supports_vision flag.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers._blocks import (
    anthropic_user_content,
    gemini_user_parts,
    openai_user_content,
)
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.base import DocumentBlock, Message
from stackowl.providers.gemini_provider import GeminiProvider
from stackowl.providers.openai_provider import OpenAIProvider

_IMG = b"\x89PNG\r\n\x1a\nFAKEPNGBYTES"
_IMG_B64 = base64.b64encode(_IMG).decode("ascii")


def _img_message() -> Message:
    block = DocumentBlock(data=_IMG, media_type="image/png", filename="x.png")
    return Message(role="user", content="What is in this image?", documents=(block,))


# --------------------------------------------------------------------------- #
# Pure block builders — the three native shapes.
# --------------------------------------------------------------------------- #
def test_anthropic_image_block_shape() -> None:
    content = anthropic_user_content(_img_message())
    image = next(b for b in content if b["type"] == "image")
    assert image == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": _IMG_B64},
    }
    # Text follows the image, as a text block.
    assert {"type": "text", "text": "What is in this image?"} in content


def test_anthropic_pdf_becomes_document_block() -> None:
    block = DocumentBlock(data=b"%PDF-1.4", media_type="application/pdf")
    content = anthropic_user_content(Message(role="user", content="read", documents=(block,)))
    assert any(b["type"] == "document" for b in content)


def test_malformed_bare_image_mime_is_not_an_image_block() -> None:
    """A subtype-less ``image/`` MIME must NOT mis-route as an image block (FIX 4)."""
    block = DocumentBlock(data=b"junk", media_type="image/")
    content = anthropic_user_content(Message(role="user", content="x", documents=(block,)))
    assert all(b["type"] != "image" for b in content)
    assert any(b["type"] == "document" for b in content)


def test_openai_image_url_data_url_shape() -> None:
    parts = openai_user_content(_img_message())
    image = next(p for p in parts if p["type"] == "image_url")
    assert image["image_url"]["url"] == f"data:image/png;base64,{_IMG_B64}"
    assert {"type": "text", "text": "What is in this image?"} in parts


def test_gemini_inline_data_shape() -> None:
    parts = gemini_user_parts(_img_message())
    inline = next(p for p in parts if "inline_data" in p)
    assert inline == {"inline_data": {"mime_type": "image/png", "data": _IMG_B64}}
    assert {"text": "What is in this image?"} in parts


# --------------------------------------------------------------------------- #
# supports_vision flips on the configured model.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("protocol", "vision_model", "text_model", "cls"),
    [
        ("openai", "gpt-4o", "gpt-3.5-turbo", OpenAIProvider),
        ("openai", "llava", "llama3.2", OpenAIProvider),
        ("anthropic", "claude-sonnet-4-6", "claude-2.1", AnthropicProvider),
        ("gemini", "gemini-2.5-pro", "gemini-1.0-pro", GeminiProvider),
    ],
)
def test_supports_vision_flag(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    vision_model: str,
    text_model: str,
    cls: type,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    def _mk(model: str) -> Any:
        cfg = ProviderConfig(
            name="p", protocol=protocol, default_model=model, tier="standard",
            base_url="http://x" if protocol == "openai" else None,
        )
        return cls(cfg, api_key="k")

    assert _mk(vision_model).supports_vision is True
    assert _mk(text_model).supports_vision is False


# --------------------------------------------------------------------------- #
# End-to-end: complete() serializes an image into the request the SDK receives.
# --------------------------------------------------------------------------- #
class _Usage:
    prompt_tokens = 1
    completion_tokens = 1
    input_tokens = 1
    output_tokens = 1


class _OAIMsg:
    content = "a cat"


class _OAIChoice:
    message = _OAIMsg()


class _OAIResp:
    choices = [_OAIChoice()]
    usage = _Usage()
    model = "gpt-4o"


class _OAICompletions:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _OAIResp:
        self.last_kwargs = kwargs
        return _OAIResp()


class _OAIChat:
    def __init__(self, c: _OAICompletions) -> None:
        self.completions = c


class _OAIClient:
    def __init__(self) -> None:
        self.chat = _OAIChat(_OAICompletions())


@pytest.mark.asyncio
async def test_openai_complete_sends_image_part(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    cfg = ProviderConfig(
        name="oai", protocol="openai", default_model="gpt-4o", tier="standard",
        base_url="http://x",
    )
    provider = OpenAIProvider(cfg, api_key="k")
    client = _OAIClient()
    provider._client = client  # type: ignore[assignment]

    await provider.complete([_img_message()], model="gpt-4o")

    sent = client.chat.completions.last_kwargs
    assert sent is not None
    content = sent["messages"][0]["content"]
    assert isinstance(content, list)
    assert any(
        p.get("type") == "image_url"
        and p["image_url"]["url"].startswith("data:image/png;base64,")
        for p in content
    )


@pytest.mark.asyncio
async def test_openai_complete_plain_message_stays_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message without blocks keeps the cheap string content (no regression)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    cfg = ProviderConfig(
        name="oai", protocol="openai", default_model="gpt-4o", tier="standard",
        base_url="http://x",
    )
    provider = OpenAIProvider(cfg, api_key="k")
    client = _OAIClient()
    provider._client = client  # type: ignore[assignment]

    await provider.complete([Message(role="user", content="hi")], model="gpt-4o")

    content = client.chat.completions.last_kwargs["messages"][0]["content"]  # type: ignore[index]
    assert content == "hi"
