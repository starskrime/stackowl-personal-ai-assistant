"""IntegrationAdapter ABC — base contract for all external service integrations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from stackowl.brief.models import BriefSection
from stackowl.health.status import HealthStatus


class ActionResult:
    """Result of an execute_action call."""

    def __init__(
        self,
        status: str,  # "ok", "requires_confirmation", "error"
        output: str = "",
        confirmation_prompt: str | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.output = output
        self.confirmation_prompt = confirmation_prompt
        self.error = error


class IntegrationAdapter(ABC):
    """Abstract base for all external service integrations (Gmail, Calendar, etc.)."""

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Unique lowercase service identifier (pattern: ^[a-z][a-z0-9_]*$)."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Initiate authentication/OAuth flow."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Return True if the adapter has valid credentials."""
        ...

    @abstractmethod
    async def refresh_credentials(self) -> None:
        """Attempt to refresh credentials (e.g. OAuth token refresh)."""
        ...

    @abstractmethod
    async def get_morning_brief_section(self) -> BriefSection | None:
        """Return a BriefSection for the morning brief, or None if unavailable."""
        ...

    @abstractmethod
    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        """Execute a named action with params. Raises UnsupportedActionError for unknown actions."""
        ...

    async def delete_credentials(self) -> bool:
        """Remove stored credentials for this integration.

        Returns ``True`` when credentials were present and removed, ``False``
        when nothing was stored (so no credentials were actually deleted).

        Default implementation returns ``False`` (no credentials to remove).
        Concrete adapters that own an OAuthManager override this to delegate
        to ``self._oauth.delete()`` and return based on whether creds existed.
        """
        return False

    # HealthContributor structural compliance
    @property
    def contributor_name(self) -> str:
        return f"integration.{self.service_name}"

    async def health_check(self) -> HealthStatus:
        connected = await self.is_connected()
        return HealthStatus(
            name=self.contributor_name,
            status="ok" if connected else "down",
            message=None,
            latency_ms=0.0,
        )
