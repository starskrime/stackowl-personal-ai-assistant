"""BrowserSettings — Camoufox runtime + session policy config."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from stackowl.paths import StackowlHome


class ProxyConfig(BaseModel):
    server: str
    username: str | None = None
    password: str | None = None
    bypass: str | None = None


class BrowserSettings(BaseModel):
    """Per-process Camoufox runtime + session-management policy.

    All paths default to ``StackowlHome.*`` derivatives so nothing is ever
    written inside the project directory at runtime.
    """

    # --- Camoufox launch knobs ---
    headless_mode: Literal["true", "virtual", "false"] = "virtual"
    humanize: bool = True
    block_images: bool = True
    block_webrtc: bool = True
    geoip: bool = True
    cache_enabled: bool = True
    disable_coop: bool = True  # Cloudflare Turnstile compatibility
    addons: list[Path] = Field(default_factory=list)
    firefox_user_prefs: dict[str, Any] = Field(default_factory=dict)
    fingerprint_rotation: Literal["per_session", "per_profile", "fixed"] = "per_profile"

    # --- Recycling / lifecycle ---
    nav_recycle_threshold: int = 200
    idle_recycle_minutes: int = 30

    # --- Session registry ---
    session_idle_timeout_minutes: int = 30
    max_concurrent_sessions: int = 8
    max_concurrent_pages_per_session: int = 4
    # A JS dialog (alert/confirm/prompt/beforeunload) blocks the page until acted
    # upon; if no browser_dialog action arrives within this window we auto-dismiss
    # so the page never hangs (self-healing). Config, not a magic constant.
    dialog_auto_dismiss_seconds: float = 60.0

    # --- Paths (resolved lazily so test fixtures can override STACKOWL_HOME) ---
    screenshots_dir: Path = Field(default_factory=StackowlHome.screenshots_dir)
    profiles_dir: Path = Field(default_factory=StackowlHome.browser_profiles_dir)
    downloads_dir: Path = Field(default_factory=StackowlHome.downloads_dir)
    browser_cache_dir: Path = Field(default_factory=StackowlHome.browser_cache_dir)

    # --- Proxy ---
    default_proxy: ProxyConfig | None = None

    # --- Memory integration ---
    enable_memory_caching: bool = True
    enable_screenshot_captions: bool = False

    # --- Inner-LLM browse meta-tool ---
    inner_browse_max_steps: int = 20
    inner_browse_model_tier: Literal["fast", "standard", "powerful"] = "standard"

    # --- Anti-bot ---
    per_domain_rate_limit_seconds: float = 2.0

    # --- Debug ---
    enable_har_recording: bool = False
