"""WhatsAppSettings — typed configuration for the WhatsApp channel adapter."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WhatsAppSettings(BaseModel):
    """WhatsApp channel configuration (Playwright-driven WhatsApp Web).

    Attributes:
        allowed_phone_numbers: Allowlist of phone numbers in E.164 format.
            An empty frozenset means "no users are allowed" — fail-closed.
        session_dir: Path to persist browser storage state. When empty the
            adapter resolves it as ``{data_dir}/whatsapp/``.
        headless: Run Chromium headless. Set ``False`` to debug QR scan.
        suppress_evolution_events: When ``True`` no owl evolution notifications
            are surfaced to WhatsApp.
        page_load_timeout_ms: Timeout for WhatsApp Web page load in ms.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_phone_numbers: frozenset[str] = Field(default_factory=frozenset)
    session_dir: str = ""
    headless: bool = True
    suppress_evolution_events: bool = False
    page_load_timeout_ms: int = 30_000
    # Gate for orchestrator startup wiring (F004-part2). Defaults False so the
    # channel is never accidentally started before its send path + consent
    # prompter are wired — documents the gap rather than hiding it.
    enabled: bool = False
