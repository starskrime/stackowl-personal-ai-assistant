"""Tests for the ``owls_list`` tool — read-only survey of configured owls.

owl_build has no query/list action (create/edit/retire only); a "check what
owls already exist" request had nowhere to go except a failing owl_build
call missing required fields. owls_list closes that gap.
"""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest, TriggerSpec
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.meta.owls_list import OwlsListTool


def _manifest(name: str, *, lifecycle: str = "on_demand", trigger: TriggerSpec | None = None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role=f"{name} role",
        system_prompt="p",
        model_tier="fast",
        lifecycle=lifecycle,
        trigger=trigger,
        origin="agent",
        created_by="secretary",
        creation_ceiling=BoundsSpec(tools=frozenset()),
        bounds=BoundsSpec(tools=frozenset()),
    )


async def test_owls_list_enumerates_configured_owls() -> None:
    registry = OwlRegistry()
    registry.register(_manifest("secretary"), source_name="t")
    registry.register(_manifest("Brain"), source_name="t")
    token = set_services(StepServices(owl_registry=registry))
    try:
        result = await OwlsListTool().execute()
    finally:
        reset_services(token)
    assert result.success
    assert "secretary" in result.output
    assert "Brain" in result.output
    assert "2 owl(s)" in result.output


async def test_owls_list_shows_schedule_for_scheduled_owl() -> None:
    registry = OwlRegistry()
    registry.register(
        _manifest(
            "Brain", lifecycle="scheduled",
            trigger=CronTrigger(schedule="daily@09:00", prompt="do the daily brief"),
        ),
        source_name="t",
    )
    token = set_services(StepServices(owl_registry=registry))
    try:
        result = await OwlsListTool().execute()
    finally:
        reset_services(token)
    assert result.success
    assert "daily@09:00" in result.output


async def test_owls_list_empty_registry_is_success_not_error() -> None:
    registry = OwlRegistry()
    token = set_services(StepServices(owl_registry=registry))
    try:
        result = await OwlsListTool().execute()
    finally:
        reset_services(token)
    assert result.success
    assert "no owls" in result.output.lower()


async def test_owls_list_no_registry_configured_degrades_to_error() -> None:
    token = set_services(StepServices(owl_registry=None))
    try:
        result = await OwlsListTool().execute()
    finally:
        reset_services(token)
    assert result.success is False
    assert "registry" in (result.error or "").lower()
