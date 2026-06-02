"""Settings — single source of truth for all StackOwl configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from stackowl.channels.discord.settings import DiscordSettings
from stackowl.channels.slack.settings import SlackSettings
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.browser import BrowserSettings
from stackowl.config.notification_settings import (
    NotificationSettings,
    QuietHoursSettings,
)
from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.config.ui_settings import UISettings
from stackowl.config.webhook_settings import (
    WebhookSettings,
    WebhookSourceConfig,
)
from stackowl.mcp.server_settings import McpServerSettings
from stackowl.mcp.settings import McpClientSettings
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.paths import StackowlHome

__all__ = ["BriefSettings", "BudgetSettings", "DiscordSettings", "GovernanceSettings", "ImageSettings", "MemorySettings", "NotificationSettings", "OrchestratorSettings", "ParliamentSettings", "QuietHoursSettings", "SandboxSettings", "SchedulerSettings", "Settings", "SlackSettings", "SystemSettings", "TelegramSettings", "TtsSettings", "UISettings", "WebhookSettings", "WebhookSourceConfig", "WebSearchSettings", "WhatsAppSettings"]  # noqa: E501

log = logging.getLogger("stackowl.config")

_DEFAULT_CONFIG_FILE = "stackowl.yaml"


class _YamlSource(PydanticBaseSettingsSource):
    """Loads settings from a YAML file (lower priority than env vars)."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._path = yaml_path
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception as exc:
            log.warning("[config] Failed to parse %s: %s", self._path, exc)
            return {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, self.field_is_complex(field)

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v is not None}


class BudgetSettings(BaseModel):
    """Spending limits enforced by :class:`CostTracker`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_limit_usd: float | None = Field(
        default=None,
        description="Daily spend cap in USD. None = unlimited.",
        json_schema_extra={"hot_reload": True},
    )
    per_turn_pause_usd: float | None = Field(
        default=0.50,
        description=(
            "Soft per-turn budget in USD. When a single interactive turn's "
            "accumulated LLM spend crosses this, the assistant ASKS the user "
            "(Continue / Stop) before any further expensive op (delegation / "
            "mixture-of-agents) runs. This is a SOFT pause (asks, never raises) "
            "and is distinct from daily_limit_usd (a hard cap). Interactive-only: "
            "background runs (cron/heartbeat/parliament/delegation children) are "
            "never paused. None = the per-turn pause is disabled."
        ),
        json_schema_extra={"hot_reload": True},
    )


class WebSearchSettings(BaseModel):
    """Web-search provider configuration (SearXNG instance, etc.).

    DDG is the keyless zero-config floor and needs no settings. SearXNG and Brave are
    configured upgrades: ``searxng_base_url`` enables the self-hosted SearXNG provider.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    searxng_base_url: str = Field(
        default="",
        description=(
            "Base URL of a self-hosted SearXNG instance (e.g. 'http://localhost:8080'). "
            "Empty disables the SearXNG provider. Network-free availability check."
        ),
        json_schema_extra={"hot_reload": True},
    )
    brave_api_key: str = Field(
        default="",
        description=(
            "Secret REFERENCE for the Brave Search API key — an env-var name, "
            "'keychain:<service>', or 'file:<path>' (resolved at use, never the raw "
            "secret). Empty disables the Brave provider. Network-free availability check."
        ),
        json_schema_extra={"hot_reload": True},
    )


class TtsSettings(BaseModel):
    """Text-to-speech configuration (E10-S3) — self-hosted-first, cloud opt-in.

    The local OSS TTS engine ('piper') is the default + the only thing ON by
    default. The cloud fallback is DISABLED unless ``cloud_enabled`` is True AND a
    key reference is configured — and even then it is only used when the local
    engine is unavailable (selector is local-first).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine: Literal["piper", "auto"] = Field(
        default="piper",
        description=(
            "TTS engine selection. 'piper' = local OSS engine only. 'auto' = local "
            "first, then the cloud fallback IF it is enabled + configured."
        ),
        json_schema_extra={"hot_reload": True},
    )
    voice: str = Field(
        default="en_US-lessac-medium",
        description="Default local-engine voice id (overridable per call).",
        json_schema_extra={"hot_reload": True},
    )
    voice_base_url: str = Field(
        default="",
        description=(
            "Override host for downloading local voice models (air-gapped / "
            "self-hosted mirror). Empty = the engine's published voice host."
        ),
        json_schema_extra={"hot_reload": False},
    )
    cloud_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in switch for the cloud TTS fallback. False (default) = local "
            "engine only; the text never leaves the machine."
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_api_key: str = Field(
        default="",
        description=(
            "Secret REFERENCE for the cloud TTS endpoint key — an env-var name, "
            "'keychain:<service>', or 'file:<path>' (resolved at use, never the raw "
            "secret). Empty disables the cloud fallback regardless of cloud_enabled."
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_base_url: str = Field(
        default="https://api.openai.com/v1",
        description=(
            "Base URL of the OpenAI-compatible speech endpoint for the cloud "
            "fallback. Point this at a self-hosted endpoint to keep audio on-prem."
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_voice: str = Field(
        default="alloy",
        description="Default voice id for the cloud fallback.",
        json_schema_extra={"hot_reload": True},
    )
    cloud_model: str = Field(
        default="tts-1",
        description="Model id sent to the cloud speech endpoint.",
        json_schema_extra={"hot_reload": True},
    )


class ImageSettings(BaseModel):
    """Image-generation configuration (E10-S4) — self-hosted-first, cloud opt-in.

    LOCKED operator decision: local SDXL runs ONLY where a capability probe clears
    (x86 + CUDA + enough memory/disk). On an incapable host (e.g. a Tegra/unified-
    memory board) the probe says no and image generation is cloud-only IF a key is
    configured, else an HONEST "unavailable". 'auto' (default) = probe → local-or-
    cloud-or-unavailable; the cloud fallback is DISABLED unless ``cloud_enabled``
    is True AND a key reference is configured (selector is local-first).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine: Literal["auto", "local", "cloud"] = Field(
        default="auto",
        description=(
            "Image engine selection. 'auto' = probe → local SDXL if viable, else "
            "the cloud fallback IF enabled + configured. 'local' = local-only "
            "(never cloud). 'cloud' = cloud-only (skip the local probe)."
        ),
        json_schema_extra={"hot_reload": True},
    )
    model: str = Field(
        default="stabilityai/sdxl-turbo",
        description="Local OSS image model id (used only where the probe clears).",
        json_schema_extra={"hot_reload": False},
    )
    size: str = Field(
        default="1024x1024",
        description="Default output size 'WIDTHxHEIGHT' (overridable per call).",
        json_schema_extra={"hot_reload": True},
    )
    cloud_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in switch for the cloud image fallback. False (default) = local "
            "only; the prompt never leaves the machine."
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_api_key: str = Field(
        default="",
        description=(
            "Secret REFERENCE for the cloud image endpoint key — an env-var name, "
            "'keychain:<service>', or 'file:<path>' (resolved at use, never the raw "
            "secret). Empty disables the cloud fallback regardless of cloud_enabled."
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_base_url: str = Field(
        default="https://api.openai.com/v1",
        description=(
            "Base URL of the OpenAI-compatible image endpoint for the cloud "
            "fallback. Point this at a self-hosted endpoint to keep prompts on-prem."
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_model: str = Field(
        default="dall-e-3",
        description="Model id sent to the cloud image endpoint.",
        json_schema_extra={"hot_reload": True},
    )


class OrchestratorSettings(BaseModel):
    """Pipeline orchestration backend selection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: Literal["langgraph", "asyncio"] = Field(
        default="asyncio",
        description=(
            "Pipeline orchestration backend. 'asyncio' is the default known-good "
            "backend; 'langgraph' is experimental/opt-in (the LangGraph backend "
            "exists but is exercised only when explicitly selected)."
        ),
        json_schema_extra={"hot_reload": False},
    )


class ParliamentSettings(BaseModel):
    """Configuration for multi-owl Parliament debate sessions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rounds: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum debate rounds per Parliament session.",
        json_schema_extra={"hot_reload": True},
    )
    session_timeout_s: float = Field(
        default=90.0,
        gt=0.0,
        description="Hard wall-clock timeout for a full Parliament session.",
        json_schema_extra={"hot_reload": True},
    )
    per_owl_timeout_s: float = Field(
        default=30.0,
        gt=0.0,
        description="Per-owl timeout for a single round.",
        json_schema_extra={"hot_reload": True},
    )
    token_budget: int = Field(
        default=20_000,
        ge=1_000,
        description="Cumulative output-character budget per owl before truncation.",
        json_schema_extra={"hot_reload": True},
    )
    convergence_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Mean pairwise cosine-similarity threshold for early termination.",
        json_schema_extra={"hot_reload": True},
    )


class MemorySettings(BaseModel):
    """Configuration for the memory knowledge pipeline (Epic 6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    semantic_search_enabled: bool = True
    sensitive_categories: list[str] = Field(
        default_factory=lambda: [
            "password",
            "ssn",
            "credit_card",
            "api_key",
            "private_key",
        ]
    )
    promotion_confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    reinforcement_required: int = Field(default=3, ge=1)
    # Corroborations (re-derivations by the conversation miner) before a mined
    # conversation fact promotes to long-term committed memory.
    # 1 = commit after being seen in a second dream pass.
    conversation_fact_reinforcement_required: int = Field(default=1, ge=0)
    # Cosine-similarity threshold (over stored embeddings) above which a newly
    # mined conversation_fact is treated as a re-derivation of an existing staged
    # fact and REINFORCES it instead of staging a near-duplicate. A reworded
    # re-extraction ("User lives in Baku" vs "The user is based in Baku") embeds
    # close enough to clear this bar. Falls back to exact-content match when an
    # embedding is unavailable. 1.0 = identical vectors required (effectively
    # exact); lower = more aggressive merging.
    conversation_fact_dedup_similarity: float = Field(default=0.92, ge=0.0, le=1.0)
    prune_after_days: int = Field(default=90, ge=1)
    extraction_after_n_messages: int = Field(default=5, ge=1)
    per_user_ceiling_bytes: int = Field(default=52_428_800, ge=1_000_000)
    short_term_window: int = Field(default=6, ge=0)
    # DreamWorker consolidation cadence: run every N minutes (config-driven, no
    # hardcoded literal in the schedule seed).
    dream_worker_interval_minutes: int = Field(default=30, ge=1)
    # Settle window: only mine/promote staged data older than this many minutes,
    # so in-flight conversation turns aren't consolidated prematurely.
    dream_worker_settle_minutes: int = Field(default=15, ge=0)


class SchedulerSettings(BaseModel):
    """Configuration for the JobScheduler (Story 7.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_concurrent_jobs: int = Field(
        default=3,
        ge=1,
        description="Maximum number of jobs allowed to run concurrently.",
        json_schema_extra={"hot_reload": True},
    )
    replay_window_hours: int = Field(
        default=24,
        ge=0,
        description="How many hours back to replay missed jobs on recovery.",
        json_schema_extra={"hot_reload": True},
    )
    max_notifications_per_hour: int = Field(
        default=10,
        ge=0,
        description="Cap on user notifications emitted per hour by background agents.",
        json_schema_extra={"hot_reload": True},
    )


class BriefSettings(BaseModel):
    """Configuration for the morning brief (Story 7.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule: str = Field(
        default="daily@08:00",
        description="Default delivery cadence for the morning brief job.",
        json_schema_extra={"hot_reload": False},
    )
    channels: list[str] = Field(
        default_factory=lambda: ["cli"],
        description="Channel identifiers that should receive the rendered brief.",
        json_schema_extra={"hot_reload": True},
    )
    sections: dict[str, bool] = Field(
        default_factory=lambda: {
            "date_and_priorities": True,
            "memory_highlights": True,
            "pending_staged": True,
            "agent_status": True,
        },
        description="Per-section toggle map keyed by assembler key.",
        json_schema_extra={"hot_reload": True},
    )


class SystemSettings(BaseModel):
    """Process-level system parameters (locale, timezone)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timezone: str = Field(
        default="UTC",
        description="IANA timezone identifier used for user-facing timestamps.",
        json_schema_extra={"hot_reload": True},
    )


class GovernanceSettings(BaseModel):
    """Audit and governance parameters (Story 12.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_retention_days: int = Field(
        default=90,
        ge=1,
        description="Number of days to keep audit_log rows before pruning.",
        json_schema_extra={"hot_reload": False},
    )
    audit_export_key: str = Field(
        default="",
        description="HMAC-SHA256 key used to sign audit log exports. Empty = unsigned.",
        json_schema_extra={"hot_reload": False},
    )
    same_day_security_prs: bool = Field(
        default=True,
        description="Auto-merge Dependabot security PRs on the same day they open.",
        json_schema_extra={"hot_reload": False},
    )


class SandboxSettings(BaseModel):
    """Sandboxed code-execution backends (E11).

    Two isolation backends: rootless ``bwrap`` (the PRIMARY — an escape stays inside
    the unprivileged user) and ``docker`` (the network-capable tier). The host's
    Docker is rootful, so a container escape can reach host root even with the full
    hardening (seccomp default-deny + cap-drop + no-new-privileges + non-root user);
    an operator may therefore prefer bwrap-only. Each flag gates whether that backend
    is offered to the selector — a disabled backend is never picked, and a run that
    needs it (e.g. a network run with ``docker_enabled=false``) gets an honest
    structured refusal, never a silent downgrade of isolation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    docker_enabled: bool = Field(
        default=True,
        description=(
            "Allow the Docker sandbox backend (the network-capable tier). False = "
            "bwrap-only; network-requiring code runs then refuse with a clear reason. "
            "Docker here is ROOTFUL, so disable this for the tightest blast radius."
        ),
        json_schema_extra={"hot_reload": True},
    )
    bwrap_enabled: bool = Field(
        default=True,
        description=(
            "Allow the rootless bubblewrap sandbox backend (the primary). False = "
            "Docker-only; if both are false, code execution is unavailable."
        ),
        json_schema_extra={"hot_reload": True},
    )


class Settings(BaseSettings):
    """Application-wide settings.

    Priority (highest to lowest): env vars (``STACKOWL_*``) → YAML file.
    The YAML file path is read from ``STACKOWL_CONFIG_FILE`` (default:
    ``stackowl.yaml`` relative to CWD).
    """

    model_config = SettingsConfigDict(
        env_prefix="STACKOWL_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    providers: list[ProviderConfig] = []
    test_mode: bool = False
    settings_watch: bool = False
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    parliament: ParliamentSettings = Field(default_factory=ParliamentSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    brief: BriefSettings = Field(default_factory=BriefSettings)
    system: SystemSettings = Field(default_factory=SystemSettings)
    ui: UISettings = Field(default_factory=UISettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    webhook: WebhookSettings = Field(default_factory=WebhookSettings)
    discord_channel: DiscordSettings = Field(default_factory=DiscordSettings)
    slack_channel: SlackSettings = Field(default_factory=SlackSettings)
    telegram_channel: TelegramSettings = Field(default_factory=TelegramSettings)
    whatsapp_channel: WhatsAppSettings = Field(default_factory=WhatsAppSettings)
    mcp_client: McpClientSettings = Field(default_factory=McpClientSettings)
    mcp_server: McpServerSettings = Field(default_factory=McpServerSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    owls: list[OwlAgentManifest] = Field(default_factory=list)
    autonomy_level: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Tool autonomy level for owls.",
        json_schema_extra={"hot_reload": True},
    )
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)
    web_search: WebSearchSettings = Field(default_factory=WebSearchSettings)
    tts: TtsSettings = Field(default_factory=TtsSettings)
    image: ImageSettings = Field(default_factory=ImageSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        config_path = StackowlHome.config_file()
        return (env_settings, _YamlSource(settings_cls, config_path))

    @model_validator(mode="after")
    def _post_init(self) -> Settings:
        config_path = StackowlHome.config_file()

        if not config_path.exists() and not self.providers:
            log.warning(
                "[config] WARNING — No providers configured; %s not found",
                config_path,
            )
        else:
            names = ", ".join(f"{p.name}[{p.tier}]" for p in self.providers if p.enabled)
            log.info("[config] Loaded providers: %s", names or "(none enabled)")

        if self.test_mode:
            TestModeGuard.activate()

        return self
