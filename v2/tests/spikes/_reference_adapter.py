"""Reference stub for IntegrationAdapter — used by ADR-17 spike tests."""
from __future__ import annotations

from typing import Any

from stackowl.brief.models import BriefSection
from stackowl.exceptions import UnsupportedActionError
from stackowl.integrations.base import ActionResult, IntegrationAdapter


class ReferenceAdapter(IntegrationAdapter):
    """Minimal reference implementation of IntegrationAdapter."""

    def __init__(self) -> None:
        self._connected = False

    @property
    def service_name(self) -> str:
        return "reference"

    async def connect(self) -> None:
        self._connected = True

    async def is_connected(self) -> bool:
        return self._connected

    async def refresh_credentials(self) -> None:
        if not self._connected:
            raise RuntimeError("Not connected")

    async def get_morning_brief_section(self) -> BriefSection | None:
        if not self._connected:
            return None
        return BriefSection(key="reference", title="Reference", items=["item 1"])

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        if action == "ping":
            return ActionResult(status="ok", output="pong")
        raise UnsupportedActionError(self.service_name, action)
