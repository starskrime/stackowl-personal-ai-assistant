"""Regression guard: the process-global TestModeGuard latch must not leak.

``Settings._post_init()`` calls ``TestModeGuard.activate()`` whenever a loaded
config has ``test_mode=True``. The latch is a class-level flag with no symmetric
deactivation, so without a restore fixture it leaks into every later test in the
same process. Observed symptom: ``tests/journeys/commands/`` (which loads
``test_mode: True`` configs) poisoned ``tests/pipeline/`` durable + drift suites
with ``TestModeViolation: Live I/O blocked in test mode``.

The fix is the autouse ``_restore_test_mode_guard`` fixture in
``tests/conftest.py`` which snapshots and restores the flag around each test.

These two tests run in definition order. The first is the positive control —
it proves loading a ``test_mode=True`` config DOES set the latch (gun loaded).
The second asserts the latch is back to ``False`` on entry — which only holds
when the conftest restore fixture is present.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard


def test_loading_test_mode_config_activates_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: a test_mode=True config activates the global latch."""
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump({"test_mode": True, "providers": []}), encoding="utf-8"
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))

    Settings()

    assert TestModeGuard.is_active() is True


def test_guard_not_leaked_from_prior_test() -> None:
    """The latch set by the previous test must not be visible here.

    Without the conftest restore fixture this fails — the prior test leaves
    ``TestModeGuard._active`` set for the rest of the process.
    """
    assert TestModeGuard.is_active() is False
