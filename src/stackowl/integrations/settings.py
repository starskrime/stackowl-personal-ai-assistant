"""IntegrationSettings — top-level configuration for the integrations package."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class IntegrationSettings(BaseModel):
    """Top-level settings controlling which integrations are enabled.

    Attributes:
        master_key: Passphrase used to derive the AES-256 encryption key for
            credential storage.  If ``None``, credential encryption will fail.
        gmail_enabled: Whether to activate the Gmail adapter on startup.
        calendar_enabled: Whether to activate the Google Calendar adapter on startup.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    master_key: str | None = None
    gmail_enabled: bool = False
    calendar_enabled: bool = False
