"""Task 4 sub-part A — editing a builtin/human owl's tier/specialty works
directly (no agent-authority ratchet), preserving /owls edit's historical scope."""
from __future__ import annotations

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.meta.owl_build import OwlBuildTool

pytestmark = pytest.mark.asyncio


async def test_edit_builtin_owl_tier() -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="scout", role="research-scout", system_prompt="p",
            model_tier="fast", origin="builtin",
        ),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=None))
    try:
        result = await OwlBuildTool().execute(action="edit", name="scout", model_tier="powerful")
        assert result.success, result.error
        assert reg.get("scout").model_tier == "powerful"
    finally:
        reset_services(token)


async def test_edit_refuses_another_agents_owl() -> None:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="helper", role="r", system_prompt="p", model_tier="fast",
            origin="agent", created_by="other_owl",
        ),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=reg, db_pool=None))
    try:
        result = await OwlBuildTool().execute(action="edit", name="helper", model_tier="powerful")
        assert not result.success
        assert "you may only modify owls you created" in result.error
    finally:
        reset_services(token)
