"""IntegrationSectionAssembler — collects brief sections from all connected integrations (Story 11.4)."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from stackowl.brief.models import BriefSection

if TYPE_CHECKING:
    from stackowl.brief.assemblers import BriefContext
    from stackowl.integrations.registry import IntegrationRegistry

log = logging.getLogger("stackowl.integrations")

_TIMEOUT_SECONDS = 10.0


class IntegrationSectionAssembler:
    """Gathers morning brief sections from all connected IntegrationAdapter instances."""

    key = "integrations"

    def __init__(self, integration_registry: IntegrationRegistry) -> None:
        # 1. ENTRY
        log.debug("integrations.integration_assembler.__init__: entry")
        self._registry = integration_registry
        # 4. EXIT
        log.debug("integrations.integration_assembler.__init__: exit")

    async def assemble(self, ctx: BriefContext) -> BriefSection:
        # 1. ENTRY
        log.debug("integrations.integration_assembler.assemble: entry")

        adapters = await self._registry.list_connected()

        # 2. DECISION — no adapters connected
        if not adapters:
            log.debug(
                "integrations.integration_assembler.assemble: decision — no adapters connected"
            )
            return BriefSection(key=self.key, title="Integrations", items=[], omitted=True)

        # 3. STEP — fetch sections from each adapter
        log.debug(
            "integrations.integration_assembler.assemble: step — fetching sections",
            extra={"_fields": {"adapter_count": len(adapters)}},
        )
        items: list[str] = []
        for adapter in adapters:
            try:
                section = await asyncio.wait_for(
                    adapter.get_morning_brief_section(),
                    timeout=_TIMEOUT_SECONDS,
                )
                if section is not None and section.items:
                    items.extend(section.items)
            except asyncio.TimeoutError:
                log.warning(
                    "integrations.integration_assembler.assemble: adapter timed out",
                    extra={"_fields": {"service": adapter.service_name}},
                )
                items.append(f"[{adapter.service_name}: timed out]")
            except Exception as exc:
                log.error(
                    "integrations.integration_assembler.assemble: adapter failed",
                    exc_info=exc,
                    extra={"_fields": {"service": adapter.service_name}},
                )

        result = BriefSection(
            key=self.key,
            title="Integrations",
            items=items,
            omitted=not items,
        )

        # 4. EXIT
        log.debug(
            "integrations.integration_assembler.assemble: exit",
            extra={"_fields": {"item_count": len(items)}},
        )
        return result
