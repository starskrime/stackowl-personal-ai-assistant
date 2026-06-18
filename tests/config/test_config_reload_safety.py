"""CFG-1 (F017) — a broken config is rejected; previous settings kept; alerted.

A YAML *parse error of an existing file* must NOT silently fall back to ``{}``
(which produced an all-defaults Settings that emptied the providers list). The
``_YamlSource`` now distinguishes missing→``{}`` from existing-but-unparseable→
raise, so a reload is genuinely rejected and the prior Settings retained. The
rejection is a health-visible ``log.error`` event, not a buried WARNING.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from stackowl.config.settings import Settings, _YamlSource
from stackowl.config.watcher import ConfigWatcher
from stackowl.events.bus import EventBus
from stackowl.exceptions import ConfigurationError


def test_yaml_source_missing_file_returns_empty(tmp_path: Path) -> None:
    src = _YamlSource(Settings, tmp_path / "absent.yaml")
    assert src() == {}


def test_yaml_source_parse_error_of_existing_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "stackowl.yaml"
    # Unbalanced bracket / bad indentation → YAML parse error.
    bad.write_text("providers: [unterminated\n  : : :\n")
    with pytest.raises(ConfigurationError):
        _YamlSource(Settings, bad)


def test_reload_rejected_keeps_prior_and_logs_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bus = EventBus()
    received: list[object] = []
    bus.subscribe("settings_reloaded", received.append)

    def _boom_factory() -> object:
        raise ConfigurationError("stackowl.yaml failed to parse")

    watcher = ConfigWatcher(
        tmp_path / "stackowl.yaml", bus, _boom_factory, poll_interval=5.0
    )
    with caplog.at_level(logging.ERROR, logger="stackowl.config"):
        watcher._reload()

    # Rejected: no settings_reloaded emitted (prior settings retained by the
    # consumers, which never received a new payload).
    assert received == []
    # Operator alerted at ERROR (health-visible), not a buried WARNING.
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_reload_success_emits(tmp_path: Path) -> None:
    bus = EventBus()
    got: list[object] = []
    bus.subscribe("settings_reloaded", got.append)
    sentinel = object()
    watcher = ConfigWatcher(
        tmp_path / "stackowl.yaml", bus, lambda: sentinel, poll_interval=5.0
    )
    watcher._reload()
    assert got == [sentinel]
