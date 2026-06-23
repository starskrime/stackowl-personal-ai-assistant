"""RuntimeSettings / AutoRestartSettings — two-process split + dev hot-restart config.

These blocks gate the gateway/core process split (``split_process``) and the
code-change auto-restart of the core process (``auto_restart``). Both default OFF
so the baseline runtime is byte-identical to the single-process monolith.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.config.auto_restart_settings import AutoRestartSettings
from stackowl.config.runtime_settings import RuntimeSettings
from stackowl.config.settings import Settings


# --- defaults: split ON (dev hot-reload is the default end-state) -----------


def test_split_process_defaults_on() -> None:
    assert RuntimeSettings().split_process is True


def test_auto_restart_defaults_on() -> None:
    assert RuntimeSettings().auto_restart.enabled is True


def test_auto_restart_default_delay_is_five_minutes() -> None:
    assert AutoRestartSettings().delay_minutes == 5.0


def test_auto_restart_default_watch_paths_is_src() -> None:
    assert AutoRestartSettings().watch_paths == ["src"]


def test_auto_restart_requires_client_connected_by_default() -> None:
    assert AutoRestartSettings().require_client_connected is True


def test_auto_restart_default_grace_seconds() -> None:
    assert AutoRestartSettings().grace_seconds == 120.0


def test_auto_restart_default_poll_interval_positive() -> None:
    assert AutoRestartSettings().poll_interval_s > 0


# --- validation bounds ------------------------------------------------------


def test_delay_minutes_allows_zero() -> None:
    assert AutoRestartSettings(delay_minutes=0).delay_minutes == 0


def test_delay_minutes_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        AutoRestartSettings(delay_minutes=-1)


def test_grace_seconds_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        AutoRestartSettings(grace_seconds=-1)


def test_poll_interval_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        AutoRestartSettings(poll_interval_s=0)


def test_watch_paths_rejects_empty_list() -> None:
    with pytest.raises(ValidationError):
        AutoRestartSettings(watch_paths=[])


def test_blocks_are_frozen() -> None:
    block = AutoRestartSettings()
    with pytest.raises(ValidationError):
        block.enabled = True  # type: ignore[misc]


def test_blocks_forbid_extra_keys() -> None:
    with pytest.raises(ValidationError):
        AutoRestartSettings(bogus=1)  # type: ignore[call-arg]


# --- composition into root Settings ----------------------------------------


def test_root_settings_exposes_runtime_block() -> None:
    runtime = Settings().runtime
    assert isinstance(runtime, RuntimeSettings)
    assert runtime.split_process is True
    assert runtime.auto_restart.enabled is True
