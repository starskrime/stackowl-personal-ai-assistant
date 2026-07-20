"""Tests for ModelDiscovery.list_models — protocol dispatch, validation-by-call."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from stackowl.exceptions import ModelDiscoveryError
from stackowl.providers.model_discovery import list_models


@pytest.mark.asyncio
async def test_openai_protocol_lists_model_ids() -> None:
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(
                data=[SimpleNamespace(id="gpt-4o"), SimpleNamespace(id="gpt-4o-mini")]
            ))
        )
    )
    with patch("openai.AsyncOpenAI", return_value=fake_client) as ctor:
        result = await list_models("openai", "https://api.example.com/v1", "sk-test")
    assert result == ["gpt-4o", "gpt-4o-mini"]
    ctor.assert_called_once_with(base_url="https://api.example.com/v1", api_key="sk-test")


@pytest.mark.asyncio
async def test_anthropic_protocol_lists_model_ids() -> None:
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(
                data=[SimpleNamespace(id="claude-sonnet-4-6")]
            ))
        )
    )
    with patch("anthropic.AsyncAnthropic", return_value=fake_client):
        result = await list_models("anthropic", None, "sk-ant-test")
    assert result == ["claude-sonnet-4-6"]


@pytest.mark.asyncio
async def test_gemini_protocol_lists_model_names() -> None:
    fake_models = SimpleNamespace(list=AsyncMock(return_value=[
        SimpleNamespace(name="models/gemini-2.5-pro"),
        SimpleNamespace(name="models/gemini-2.5-flash"),
    ]))
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    with patch("google.genai.Client", return_value=fake_client):
        result = await list_models("gemini", None, "AIza-test")
    assert result == ["gemini-2.5-pro", "gemini-2.5-flash"]


@pytest.mark.asyncio
async def test_grok_protocol_dispatches_via_openai_client() -> None:
    """grok is OpenAI-compatible — mirrors _build_provider's else-branch dispatch."""
    fake_client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(
            data=[SimpleNamespace(id="grok-2")]
        )))
    )
    with patch("openai.AsyncOpenAI", return_value=fake_client):
        result = await list_models("grok", "https://api.x.ai/v1", "xai-test")
    assert result == ["grok-2"]


@pytest.mark.asyncio
async def test_failure_raises_model_discovery_error_with_provider_and_reason() -> None:
    fake_client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(side_effect=ConnectionError("refused")))
    )
    with patch("openai.AsyncOpenAI", return_value=fake_client):
        with pytest.raises(ModelDiscoveryError) as exc_info:
            await list_models("openai", "https://bad.example.com/v1", "sk-test")
    assert "refused" in str(exc_info.value)
