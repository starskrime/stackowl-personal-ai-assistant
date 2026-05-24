"""ADR-17 spike: IntegrationAdapter contract verification."""
from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.spike

_SKIP = os.environ.get("STACKOWL_RUN_SPIKES") != "1"
skip_unless_spike = pytest.mark.skipif(_SKIP, reason="Set STACKOWL_RUN_SPIKES=1 to run spikes")


@pytest.mark.asyncio
@skip_unless_spike
async def test_integration_adapter_has_all_required_methods() -> None:
    """IntegrationAdapter ABC exposes all required methods."""
    from stackowl.integrations.base import IntegrationAdapter, ActionResult
    required = ["service_name", "connect", "is_connected", "refresh_credentials",
                "get_morning_brief_section", "execute_action"]
    for method in required:
        assert hasattr(IntegrationAdapter, method), f"Missing: {method}"


@pytest.mark.asyncio
@skip_unless_spike
async def test_reference_under_100_lines() -> None:
    """Reference stub implementation fits under 100 lines."""
    import inspect
    from tests.spikes._reference_adapter import ReferenceAdapter
    source = inspect.getsource(ReferenceAdapter)
    lines = [l for l in source.splitlines() if l.strip()]
    assert len(lines) < 100, f"Reference adapter too long: {len(lines)} lines"


def test_integration_not_found_error() -> None:
    """IntegrationRegistry.get raises IntegrationNotFoundError for unknown service."""
    from stackowl.integrations.registry import IntegrationRegistry
    from stackowl.exceptions import IntegrationNotFoundError
    reg = IntegrationRegistry()
    with pytest.raises(IntegrationNotFoundError):
        reg.get("nonexistent")


def test_integration_registry_list_all_empty() -> None:
    """Fresh IntegrationRegistry.list_all() returns empty list."""
    from stackowl.integrations.registry import IntegrationRegistry
    reg = IntegrationRegistry()
    assert reg.list_all() == []


@pytest.mark.asyncio
async def test_integration_registry_list_connected_empty() -> None:
    """Fresh IntegrationRegistry.list_connected() returns empty list."""
    from stackowl.integrations.registry import IntegrationRegistry
    reg = IntegrationRegistry()
    result = await reg.list_connected()
    assert result == []


def test_action_result_construction() -> None:
    """ActionResult can be constructed with required fields."""
    from stackowl.integrations.base import ActionResult
    r = ActionResult(status="ok", output="done")
    assert r.status == "ok"
    assert r.output == "done"
    assert r.confirmation_prompt is None


def test_unsupported_action_error() -> None:
    """UnsupportedActionError carries service_name and action."""
    from stackowl.exceptions import UnsupportedActionError
    err = UnsupportedActionError("gmail", "delete_all")
    assert err.service_name == "gmail"
    assert err.action == "delete_all"


@pytest.mark.asyncio
async def test_integration_registry_register_and_get() -> None:
    """register() + get() round-trip works."""
    from stackowl.integrations.registry import IntegrationRegistry
    from tests.spikes._reference_adapter import ReferenceAdapter
    reg = IntegrationRegistry()
    adapter = ReferenceAdapter()
    reg.register(adapter)
    retrieved = reg.get("reference")
    assert retrieved is adapter
