"""AgentPauseContext — protocol stub wired to real agents in Epic 7."""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger("stackowl.db")


class AgentPauseContext(Protocol):
    """Pause/resume active agents around disruptive DB operations (e.g. migrations)."""

    def pause_for_migration(self) -> None: ...

    def resume_after_migration(self) -> None: ...


class NoOpAgentPauseContext:
    """Stub used until real agents are wired in Epic 7."""

    def pause_for_migration(self) -> None:
        log.info("[db] agent_pause: stub — no agents to pause (Epic 7 not yet wired)")

    def resume_after_migration(self) -> None:
        log.info("[db] agent_pause: stub — no agents to resume (Epic 7 not yet wired)")
