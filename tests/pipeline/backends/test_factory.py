"""Tests for the pipeline backend factory (create_backend)."""

from __future__ import annotations

import logging

import pytest

from stackowl.config.settings import OrchestratorSettings
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.backends.factory import create_backend
from stackowl.pipeline.backends.langgraph_backend import LangGraphBackend
from stackowl.pipeline.services import StepServices


def test_create_backend_langgraph_returns_langgraph() -> None:
    backend = create_backend("langgraph", services=StepServices())
    assert isinstance(backend, LangGraphBackend)


def test_create_backend_asyncio_returns_asyncio() -> None:
    backend = create_backend("asyncio", services=StepServices())
    assert isinstance(backend, AsyncioBackend)


def test_create_backend_unknown_falls_back_to_asyncio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        backend = create_backend("does-not-exist", services=StepServices())

    assert isinstance(backend, AsyncioBackend)
    # The warning must capture the offending value (the diagnostic value of the
    # fail-safe). It lives in the structured _fields, per the logging convention.
    assert any(
        getattr(record, "_fields", {}).get("requested") == "does-not-exist"
        for record in caplog.records
    )


def test_create_backend_empty_string_falls_back_to_asyncio() -> None:
    backend = create_backend("", services=StepServices())
    assert isinstance(backend, AsyncioBackend)


def test_create_backend_passes_services_through() -> None:
    # The caller's services must reach the constructed backend, not a fresh one.
    services = StepServices()
    backend = create_backend("asyncio", services=services)
    assert backend._services is services  # type: ignore[attr-defined]


def test_orchestrator_settings_default_is_asyncio() -> None:
    assert OrchestratorSettings().backend == "asyncio"
