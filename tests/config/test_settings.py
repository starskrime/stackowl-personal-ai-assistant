"""MemorySettings — DreamWorker cadence/settle-window config fields.

Verifies the config-driven cadence (interval) and settle-window (settle)
defaults and their validation bounds. Both must be config-driven (no
hardcoded literals in production logic).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.config.settings import MemorySettings


def test_dream_worker_interval_default_is_30() -> None:
    assert MemorySettings().dream_worker_interval_minutes == 30


def test_dream_worker_settle_default_is_15() -> None:
    assert MemorySettings().dream_worker_settle_minutes == 15


def test_dream_worker_interval_rejects_below_one() -> None:
    with pytest.raises(ValidationError):
        MemorySettings(dream_worker_interval_minutes=0)


def test_dream_worker_settle_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        MemorySettings(dream_worker_settle_minutes=-1)


def test_dream_worker_settle_allows_zero() -> None:
    assert MemorySettings(dream_worker_settle_minutes=0).dream_worker_settle_minutes == 0
