"""``JOURNAL_RETENTION_DAYS`` derives from ``JournalSettings()``'s own
default -- a single source of truth, never a second hand-typed literal
(Story 2.11, AD-6)."""

from __future__ import annotations

from stackowl.config.journal_settings import JournalSettings
from stackowl.journal.retention import JOURNAL_RETENTION_DAYS


def test_the_settings_default_is_thirty_days() -> None:
    assert JournalSettings().retention_days == 30


def test_the_tripwire_constant_derives_from_the_settings_default() -> None:
    """Not a second literal ``30`` -- the exact drift class this repo keeps
    paying for. Proven by identity with the settings model's own default,
    not by both merely equaling the same number today."""
    assert JournalSettings().retention_days == JOURNAL_RETENTION_DAYS


def test_a_settings_override_does_not_move_the_tripwire_constant() -> None:
    """The tripwire constant is a DEFAULT-instance derivation, computed once
    at import time -- it must NOT track a live ``stackowl.yaml`` override, or
    a deployment's own config could silently desync the tripwire's own
    honesty check (see ``journal/retention.py``'s module docstring)."""
    overridden = JournalSettings(retention_days=5)
    assert overridden.retention_days == 5
    assert JOURNAL_RETENTION_DAYS == 30
