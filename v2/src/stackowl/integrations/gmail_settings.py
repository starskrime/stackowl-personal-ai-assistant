"""GmailSettings — configuration model for the Gmail integration adapter."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GmailSettings(BaseModel):
    """Immutable configuration for the Gmail integration adapter.

    Attributes:
        enabled: Whether the Gmail integration is active.
        client_id: Google OAuth 2.0 client ID.
        client_secret: Google OAuth 2.0 client secret.
        brief_filter: Gmail search query used to populate the morning brief section.
        brief_max_items: Maximum number of messages shown in the morning brief.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    client_id: str | None = None
    client_secret: str | None = None
    brief_filter: str = "is:starred OR is:important"
    brief_max_items: int = Field(default=5, ge=1, le=50)
