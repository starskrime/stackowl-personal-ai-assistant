"""Settings — single source of truth for all StackOwl configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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
from stackowl.config.progress_settings import ProgressSettings
from stackowl.config.provider import ProviderConfig
from stackowl.config.runtime_settings import RuntimeSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.config.ui_settings import UISettings
from stackowl.config.webhook_settings import (
    WebhookSettings,
    WebhookSourceConfig,
)
from stackowl.exceptions import ConfigurationError
from stackowl.mcp.server_settings import McpServerSettings
from stackowl.mcp.settings import McpClientSettings
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.paths import StackowlHome

__all__ = ["BriefSettings", "BudgetSettings", "CheckInSettings", "DiscordSettings", "GovernanceSettings", "IdentitySettings", "ImageSettings", "MemorySettings", "NotificationSettings", "OrchestratorSettings", "ParliamentSettings", "ProgressSettings", "QuietHoursSettings", "SandboxSettings", "SchedulerSettings", "Settings", "SlackSettings", "SystemSettings", "TelegramSettings", "TranscriptionSettings", "TtsSettings", "UISettings", "WebhookSettings", "WebhookSourceConfig", "WebSearchSettings", "WhatsAppSettings"]  # noqa: E501

log = logging.getLogger("stackowl.config")

_DEFAULT_CONFIG_FILE = "stackowl.yaml"


class _YamlSource(PydanticBaseSettingsSource):
    """Loads settings from a YAML file (lower priority than env vars)."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._path = yaml_path
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        # Distinguish a MISSING file (legitimately {} — first-run defaults) from
        # an EXISTING file that fails to parse. A parse error of an existing file
        # must NOT fall back to {} (CFG-1 / F017): that silently emptied the
        # providers list to all-defaults and PASSED validation, the opposite of
        # the documented "previous settings kept" guarantee. Raise so a hot
        # reload is genuinely rejected (the watcher keeps the prior Settings) and
        # a boot-time typo fails loud instead of booting an empty config.
        if not self._path.exists():
            return {}
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("[config] Failed to parse %s: %s", self._path, exc)
            raise ConfigurationError(
                f"config file {self._path} exists but failed to parse: {exc}"
            ) from exc
        return raw if isinstance(raw, dict) else {}

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
    unknown_cloud_per_1m_usd: float = Field(
        default=15.0,
        description=(
            "Conservative price (USD per 1M tokens, applied to both input and "
            "output) charged for an unrecognized CLOUD model absent from "
            "pricing.yaml. Deliberately high so an unpriced paid model trips the "
            "budget cap rather than silently billing $0 (F128). Local/self-hosted "
            "backends are always free; this only affects unknown cloud models."
        ),
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


class ClarifySettings(BaseModel):
    """Per-channel clarify (Raise/Stop) wait-timeout policy (STEER-7/F094).

    When an interactive budget cap is hit, the gate blocks awaiting a human
    Raise/Stop answer. A single hardcoded 120s could auto-Stop a slow mobile user
    before they reply. ``wait_timeout_s`` is the global fallback (kept at 120s for
    back-compat); ``per_channel`` overrides it for specific channels (e.g. a
    generous wait for ``telegram``). Channel keys are the channel names
    (``cli``/``telegram``/``slack``/…); an unlisted channel uses ``wait_timeout_s``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    wait_timeout_s: float = Field(
        default=120.0,
        gt=0.0,
        description="Fallback seconds to await a human Raise/Stop before failing closed.",
        json_schema_extra={"hot_reload": True},
    )
    per_channel: dict[str, float] = Field(
        default_factory=dict,
        description="Per-channel clarify wait-timeout overrides (channel name → seconds).",
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


class TranscriptionSettings(BaseModel):
    """Speech-to-text (voice transcription) configuration — self-hosted-first.

    Sibling of :class:`TtsSettings` in the opposite direction (audio → text). The
    local OSS engine ('whisper', ``openai-whisper``) keeps audio on the box. The
    feature is ON by default (``enabled=True``): Telegram voice notes are
    transcribed + confirmed, and the TUI Ctrl+R push-to-talk dictates into the
    compose box. Set ``enabled=False`` to wire neither path (byte-identical to a
    build without the feature). The cloud fields are placeholders for a future
    cloud STT backend (none is shipped yet); leaving them does nothing today.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            "Master switch for voice transcription (Telegram voice notes + TUI "
            "push-to-talk). ON by default: Telegram voice notes are transcribed "
            "(local Whisper) and shown with a Send/Discard prompt, and the TUI "
            "Ctrl+R push-to-talk dictates into the compose box. Set False to wire "
            "neither path (byte-identical to a build without this feature)."
        ),
        json_schema_extra={"hot_reload": False},
    )
    engine: Literal["local", "auto"] = Field(
        default="local",
        description=(
            "STT engine selection. 'local' = local Whisper only (audio never "
            "leaves the box). 'auto' = local first, then a cloud fallback IF one "
            "is enabled + configured (no cloud STT backend ships yet)."
        ),
        json_schema_extra={"hot_reload": True},
    )
    model: str = Field(
        default="base",
        description=(
            "Local Whisper model size ('tiny', 'base', 'small', 'medium', "
            "'large'). Smaller = faster but less accurate; consider 'tiny' on a "
            "CPU-only / ARM host where 'base' is slow."
        ),
        json_schema_extra={"hot_reload": False},
    )
    language: str = Field(
        default="",
        description=(
            "Optional ISO language hint for the local engine (e.g. 'en'). Empty = "
            "let Whisper auto-detect the spoken language (multilingual)."
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in switch for a future cloud STT fallback. False (default) = "
            "local engine only; the audio never leaves the machine. (Placeholder: "
            "no cloud STT backend ships yet.)"
        ),
        json_schema_extra={"hot_reload": True},
    )
    cloud_api_key: str = Field(
        default="",
        description=(
            "Secret REFERENCE for a future cloud STT endpoint key — an env-var "
            "name, 'keychain:<service>', or 'file:<path>' (resolved at use, never "
            "the raw secret). Empty disables the cloud fallback. (Placeholder.)"
        ),
        json_schema_extra={"hot_reload": True, "sensitive": True},
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
    tool_count_cap: int = Field(
        default=40,
        ge=1,
        description=(
            "Maximum number of tools presented to the model per turn. The "
            "discretionary roster is relevance-ranked and clipped to this count; "
            "guaranteed base tools are always included and count toward it. Default "
            "40 matches the historical cap (byte-identical). Lower it (e.g. 12-18) "
            "for weak/quantized models that derail when offered too many tools."
        ),
        json_schema_extra={"hot_reload": True},
    )


class SkillsSettings(BaseModel):
    """Skill subsystem behavior."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    global_catalog: bool = Field(
        default=True,
        description=(
            "When True, every tool-using turn surfaces a CATALOG of installed "
            "skills (names only) in the system prompt so an owl that does not own "
            "a skill still knows it exists and can skill_view it. The default "
            "Secretary owns no skills, so without this it never learns skills "
            "exist. Set False to restore the byte-identical pre-feature baseline."
        ),
        json_schema_extra={"hot_reload": True},
    )


class DurableSettings(BaseModel):
    """Feature flags for the durable-ReAct execution substrate (agentic-os Stage 1).

    The durable substrate (DurableTask + side-effect ledger + per-iteration
    checkpoints) is gated OFF by default so every current code path is
    byte-for-byte unchanged. Turning ``goals`` ON routes scheduled owl goals
    (:class:`~stackowl.scheduler.handlers.goal_execution.GoalExecutionHandler`)
    through the durable pipeline: a task row is created, the drive is
    checkpointed + exactly-once-guarded, and the task ends
    ``completed``/``parked``/``failed``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    goals: bool = Field(
        default=True,
        description=(
            "Route scheduled owl goals AND objective sub-goals through the durable "
            "pipeline (checkpointed, exactly-once, resumable) so long-running "
            "autonomous work survives a restart. ON by default as of the Objective "
            "Manager keystone; set False to fall back to the legacy ephemeral path."
        ),
        json_schema_extra={"hot_reload": True},
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
    # F063 — incremental contradiction scan tuning.
    # ANN candidate count per new fact (the RIGHT side of each comparison). MUST
    # be >= 32 so the >=0.85 cross-source contradiction band isn't truncated by
    # same-source near-duplicates at ~0.99. Host-scalable upward.
    contradiction_ann_k: int = Field(default=32, ge=32)
    # Below this committed-corpus size the brute-force O(n^2) scan is used (cheap
    # at small N, behaviour-identical to the legacy path). At/above it the
    # incremental watermark + ANN-candidate path takes over.
    contradiction_ann_threshold: int = Field(default=200, ge=2)
    # MEM-1 (F073) — blended recall ranking config.
    # How many committed facts the classify-step recall presents to the prompt
    # AFTER blended ranking (the final top-K). Host-scalable upward.
    recall_limit: int = Field(default=5, ge=1)
    # Candidate over-fetch: pull this many relevance-ordered candidates BEFORE
    # blending, so recency/reinforcement can promote a fact the raw relevance
    # cut would have dropped. Must be >= recall_limit (enforced at use site).
    recall_candidate_pool: int = Field(default=20, ge=1)
    # Recency half-life in days: a fact's freshness weight halves every N days.
    # Larger = a long relationship's older facts stay competitive longer.
    recall_decay_half_life_days: float = Field(default=30.0, gt=0.0)


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


class CheckInSettings(BaseModel):
    """Configuration for the proactive check-in (heartbeat) job.

    Mirrors :class:`BriefSettings`. The morning brief has no ``enabled`` flag —
    it is effectively always on (unconditionally seeded). The check-in adds an
    explicit toggle so an operator can turn the periodic outreach off, but it
    DEFAULTS ON so the promised feature actually fires out of the box. The seed
    is HONEST: even when ``enabled`` is True, a row is only created when a single
    resolvable owner recipient exists — a default empty telegram allowlist seeds
    nothing (and warns) rather than a dead, permanently-undeliverable row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            "Whether the proactive check-in job is scheduled. ON by default so the "
            "promised periodic outreach fires; the seed still only creates a row "
            "when a single resolvable recipient exists (never a dead no-op row)."
        ),
        json_schema_extra={"hot_reload": False},
    )
    schedule: str = Field(
        default="daily@18:00",
        description="Delivery cadence for the check-in job, as daily@HH:MM (24h).",
        json_schema_extra={"hot_reload": False},
    )
    # Default to telegram, the only channel with a durable proactive recipient
    # resolver today — "cli" has no durable address, so it would leave the
    # (default-on) check-in permanently undeliverable. A cli/other-only operator
    # still gets the honest "no resolvable recipient" no-seed path.
    channels: list[str] = Field(
        default_factory=lambda: ["telegram"],
        description="Channel identifiers that should receive the check-in.",
        json_schema_extra={"hot_reload": True},
    )

    @field_validator("schedule")
    @classmethod
    def _validate_schedule(cls, value: str) -> str:
        """Reject a malformed schedule at config-load (fail loud, not at seed time).

        Only ``daily@HH:MM`` with a valid 24h time is accepted. A bad value here
        would otherwise flow into the seed and produce a wrong (or assembly-
        crashing out-of-range) first-run hour.
        """
        if not value.startswith("daily@"):
            raise ValueError("check_in.schedule must be of the form 'daily@HH:MM'")
        body = value[len("daily@"):]
        parts = body.split(":")
        if len(parts) != 2:
            raise ValueError("check_in.schedule must be 'daily@HH:MM'")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("check_in.schedule HH and MM must be integers") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("check_in.schedule HH must be 0-23 and MM 0-59")
        return value


class SystemSettings(BaseModel):
    """Process-level system parameters (locale, timezone)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timezone: str = Field(
        default="UTC",
        description="IANA timezone identifier used for user-facing timestamps.",
        json_schema_extra={"hot_reload": True},
    )


class IdentitySettings(BaseModel):
    """Cross-channel identity alias map.

    Maps a stable ``identity_key`` (e.g. ``"owner-primary"``) to a list of
    per-channel handles (e.g. ``["telegram:123456", "slack:U0ABC"]``).  An
    empty map (the default) means every handle resolves to itself — behavior
    is byte-identical to before this feature existed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Map of identity_key → list of channel handles. "
            "Example: {\"owner-primary\": [\"telegram:123\", \"slack:U9\"]}"
        ),
        # hot_reload=True: the identity resolver is shared and refreshed in place
        # by the `settings_reloaded` subscriber (startup/identity_reload.py), so
        # alias edits take effect live — no restart required.
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
    settings_watch: bool = True
    # Route the user-facing answer's escalation FLOOR off the triage router's
    # intent_class (conversational->fast, standard->standard). False => the answer
    # always starts at "fast" (legacy, byte-identical). Helpers/extractor tiers are
    # config-driven and unaffected by this flag.
    answer_floor_by_intent: bool = True
    # Tier for the delivery (deliver-vs-give-up) judge — a structured reasoning
    # call, NOT the user-facing answer. Defaults to "standard": the smallest tier
    # (a thinking model) rambles for thousands of tokens here (slow) and rules
    # give-up unreliably (wrong), so the judge needs a capable-enough tier.
    # General/config-driven — no vendor or model names; resolved via the tier
    # cascade, so it degrades gracefully if "standard" is absent.
    judge_tier: str = "standard"
    # Tier for the POST-HOC, FAIL-CLOSED LLM-derived acceptance layer (verification
    # B3). Empty (the default) ⇒ the layer is OFF: goal-level acceptance is purely
    # the deterministic declared-artifact observation. When set to a tier name, the
    # driver may derive an expected outcome from a sub-goal's draft when none was
    # declared, then OBSERVE reality — never asserting a positive pass it could not
    # measure (model unavailable ⇒ no acceptance, honest-limit). General/config-
    # driven — no vendor or model names; resolved via the tier cascade. Mirrors
    # judge_tier. Default OFF keeps every path byte-identical. The OFF state is not
    # silent (F-14): when the layer is skipped because this tier is unset, the
    # deriver logs ``acceptance_skip_reason`` so "an undeclared claim went unverified"
    # is visible in the trace rather than an invisible early-return.
    acceptance_tier: str = ""
    # ADR-1 — route the per-tool verification seam through the single AcceptanceAuthority
    # so a tool that DECLARES a PostCondition (post_condition()) has its `verified` set by
    # an observation of reality, distinct from the actor. Default False ⇒ the seam is
    # skipped entirely and every tool is byte-identical to pre-ADR behavior (the ~92 tools
    # that declare no post-condition are unaffected even when ON). Intended ON in
    # production once the gate is green (this powerful machine: correctness over latency).
    acceptance_authority: bool = False
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    durable: DurableSettings = Field(default_factory=DurableSettings)
    parliament: ParliamentSettings = Field(default_factory=ParliamentSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    brief: BriefSettings = Field(default_factory=BriefSettings)
    check_in: CheckInSettings = Field(default_factory=CheckInSettings)
    system: SystemSettings = Field(default_factory=SystemSettings)
    ui: UISettings = Field(default_factory=UISettings)
    progress: ProgressSettings = Field(default_factory=ProgressSettings)
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
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    image: ImageSettings = Field(default_factory=ImageSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    clarify: ClarifySettings = Field(default_factory=ClarifySettings)
    identity: IdentitySettings = Field(default_factory=IdentitySettings)
    skills: SkillsSettings = Field(default_factory=SkillsSettings)

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
