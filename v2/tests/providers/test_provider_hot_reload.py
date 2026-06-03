"""LIVE provider hot-reload — ProviderRegistry.apply_settings + watcher wiring.

Proves the watch→reload pipeline for providers:
- apply_settings ADDS / REMOVES / PRESERVES / REBUILDS providers in place.
- preserved providers keep their SAME CircuitBreaker + RateLimiter (state intact).
- the cost tracker is injected into newly built providers.
- the reload handler type-guards: a dict payload is ignored, a Settings payload acts.
- a real ConfigWatcher picks up a yaml edit and the registry gains the provider.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from stackowl.config.provider import ProviderConfig
from stackowl.config.settings import Settings
from stackowl.config.watcher import ConfigWatcher
from stackowl.events.bus import EventBus
from stackowl.providers.cost_tracker import CostTracker
from stackowl.providers.registry import ProviderRegistry
from stackowl.startup.provider_reload import make_settings_reload_handler


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings() at a per-test yaml so no ambient ~/.stackowl config leaks.

    Settings drops init_settings from its source chain (yaml/env only), so the
    ONLY way to build a Settings with specific providers is via the yaml file.
    """
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text("test_mode: true\nproviders: []\n", encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


def _provider(name: str, *, tier: str = "fast", model: str = "m1") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol="openai",
        enabled=True,
        base_url="http://localhost:1234",
        default_model=model,
        tier=tier,
    )


def _write_settings(cfg: Path, *providers: ProviderConfig) -> Settings:
    """Write providers to the isolated yaml and return a freshly-loaded Settings."""
    cfg.write_text(
        yaml.dump(
            {
                "test_mode": True,
                "providers": [p.model_dump(exclude_none=True) for p in providers],
            }
        ),
        encoding="utf-8",
    )
    return Settings()


# ---------------------------------------------------------------------------
# Part 1 — apply_settings
# ---------------------------------------------------------------------------


def test_apply_settings_adds_new_provider(_isolated_config: Path) -> None:
    cfg = _isolated_config
    registry = ProviderRegistry.from_settings(_write_settings(cfg, _provider("a")))
    assert "b" not in registry._providers

    registry.apply_settings(_write_settings(cfg, _provider("a"), _provider("b", tier="standard")))

    # get() works for the freshly-added provider.
    assert registry.get("b") is not None
    assert registry._tiers["b"] == "standard"


def test_apply_settings_removes_dropped_provider(_isolated_config: Path) -> None:
    cfg = _isolated_config
    registry = ProviderRegistry.from_settings(_write_settings(cfg, _provider("a"), _provider("b")))
    assert "b" in registry._providers

    registry.apply_settings(_write_settings(cfg, _provider("a")))

    assert "b" not in registry._providers
    assert "b" not in registry._tiers
    assert "b" not in registry._breakers
    assert "b" not in registry._limiters


def test_apply_settings_unchanged_preserves_breaker_and_limiter_identity(
    _isolated_config: Path,
) -> None:
    cfg = _isolated_config
    registry = ProviderRegistry.from_settings(_write_settings(cfg, _provider("a")))
    old_breaker = registry._breakers["a"]
    old_limiter = registry._limiters["a"]
    old_provider = registry._providers["a"]

    # Identical config for "a", plus a new "b".
    registry.apply_settings(_write_settings(cfg, _provider("a"), _provider("b")))

    # Same objects => circuit-breaker + rate-limiter runtime state is intact.
    assert registry._breakers["a"] is old_breaker
    assert registry._limiters["a"] is old_limiter
    assert registry._providers["a"] is old_provider


def test_apply_settings_changed_provider_rebuilds(_isolated_config: Path) -> None:
    cfg = _isolated_config
    registry = ProviderRegistry.from_settings(
        _write_settings(cfg, _provider("a", tier="fast", model="m1"))
    )
    old_breaker = registry._breakers["a"]
    old_provider = registry._providers["a"]

    # Same name, different tier + model => CHANGED, must rebuild.
    registry.apply_settings(_write_settings(cfg, _provider("a", tier="powerful", model="m2")))

    assert registry._breakers["a"] is not old_breaker
    assert registry._providers["a"] is not old_provider
    assert registry._tiers["a"] == "powerful"


def test_apply_settings_injects_cost_tracker_into_new_provider(_isolated_config: Path) -> None:
    cfg = _isolated_config
    registry = ProviderRegistry.from_settings(_write_settings(cfg, _provider("a")))
    tracker = CostTracker.__new__(CostTracker)  # sentinel object; identity-only check
    registry.set_cost_tracker(tracker)  # type: ignore[arg-type]

    registry.apply_settings(_write_settings(cfg, _provider("a"), _provider("b")))

    new_provider = registry._providers["b"]
    # OpenAIProvider stores the tracker via set_cost_tracker.
    assert getattr(new_provider, "_cost_tracker", None) is tracker


# ---------------------------------------------------------------------------
# Part 2 — reload handler type-guard
# ---------------------------------------------------------------------------


def test_reload_handler_ignores_dict_payload(_isolated_config: Path) -> None:
    registry = ProviderRegistry.from_settings(_write_settings(_isolated_config, _provider("a")))
    handler = make_settings_reload_handler(registry)

    # config_command / provider_command emit dict payloads — must be ignored.
    handler({"provider": "b"})

    assert "b" not in registry._providers  # apply_settings was NOT called


def test_reload_handler_applies_settings_payload(_isolated_config: Path) -> None:
    cfg = _isolated_config
    registry = ProviderRegistry.from_settings(_write_settings(cfg, _provider("a")))
    handler = make_settings_reload_handler(registry)

    handler(_write_settings(cfg, _provider("a"), _provider("b")))

    assert "b" in registry._providers


def test_reload_handler_swallows_apply_errors(_isolated_config: Path) -> None:
    """A bad reload must never propagate out of the handler (would kill the watcher)."""
    cfg = _isolated_config
    registry = ProviderRegistry.from_settings(_write_settings(cfg, _provider("a")))
    handler = make_settings_reload_handler(registry)

    def _explode(_settings_obj: object) -> None:
        raise RuntimeError("apply failed")

    registry.apply_settings = _explode  # type: ignore[method-assign]
    # Must not raise even though apply_settings throws.
    handler(_write_settings(cfg, _provider("a")))


# ---------------------------------------------------------------------------
# Part 3 — ConfigWatcher integration (real thread, real file edit)
# ---------------------------------------------------------------------------


def test_config_watcher_reloads_registry_on_file_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "test_mode": True,
                "providers": [
                    {
                        "name": "a",
                        "protocol": "openai",
                        "base_url": "http://localhost:1234",
                        "default_model": "m1",
                        "tier": "fast",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))

    registry = ProviderRegistry.from_settings(Settings())
    assert "b" not in registry._providers

    event_bus = EventBus()
    handler = make_settings_reload_handler(registry)
    event_bus.subscribe("settings_reloaded", handler)

    watcher = ConfigWatcher(
        config_path=cfg,
        event_bus=event_bus,
        settings_factory=lambda: Settings(),
        poll_interval=0.05,
    )
    watcher.start()
    try:
        # mtime resolution can be coarse — ensure a distinct mtime.
        time.sleep(0.1)
        cfg.write_text(
            yaml.dump(
                {
                    "test_mode": True,
                    "providers": [
                        {
                            "name": "a",
                            "protocol": "openai",
                            "base_url": "http://localhost:1234",
                            "default_model": "m1",
                            "tier": "fast",
                        },
                        {
                            "name": "b",
                            "protocol": "openai",
                            "base_url": "http://localhost:1234",
                            "default_model": "m2",
                            "tier": "standard",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if "b" in registry._providers:
                break
            time.sleep(0.05)

        assert "b" in registry._providers, "watcher did not hot-reload the new provider"
    finally:
        watcher.stop()


# ---------------------------------------------------------------------------
# Concurrency — reader on the loop thread vs apply_settings on the watcher thread
# ---------------------------------------------------------------------------


def test_concurrent_reads_during_reload_never_crash(_isolated_config: Path) -> None:
    """get_*/cascade must never KeyError when a reload swaps the maps mid-read.

    A reader thread hammers get_with_cascade/get_by_tier/get while a writer
    thread repeatedly removes and re-adds a provider via apply_settings. The
    read methods snapshot the dict refs and use .get(), so a name iterated from
    a stale _tiers can never index a freshly-swapped _providers.
    """
    import contextlib
    import threading

    from stackowl.exceptions import ProviderNotFoundError

    base = ProviderRegistry.from_settings(
        _write_settings(_isolated_config, _provider("a", tier="fast"), _provider("b", tier="standard"))
    )
    full = _write_settings(_isolated_config, _provider("a", tier="fast"), _provider("b", tier="standard"))
    just_a = _write_settings(_isolated_config, _provider("a", tier="fast"))

    errors: list[BaseException] = []
    stop = threading.Event()

    def _reader() -> None:
        while not stop.is_set():
            try:
                base.get_with_cascade("standard")
                base.get_by_tier("standard")
                # ProviderNotFoundError is an acceptable outcome (b may be gone);
                # a KeyError is the crash we are guarding against and must escape.
                with contextlib.suppress(ProviderNotFoundError):
                    base.get("b")
            except KeyError as exc:  # the bug we are guarding against
                errors.append(exc)
                return

    def _writer() -> None:
        for _ in range(150):
            base.apply_settings(just_a)  # removes b
            base.apply_settings(full)  # re-adds b
            if stop.is_set():
                return

    readers = [threading.Thread(target=_reader) for _ in range(3)]
    writer = threading.Thread(target=_writer)
    for t in readers:
        t.start()
    writer.start()
    writer.join(timeout=15.0)
    stop.set()
    for t in readers:
        t.join(timeout=5.0)

    assert not errors, f"concurrent read during reload raised: {errors[:3]}"
