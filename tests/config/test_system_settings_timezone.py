"""SystemSettings.timezone — must resolve as a real IANA zone.

Before this validator, an unresolvable string (a typo, or a malformed
/config detect-timezone result) would sit silently in system.timezone until
compute_next_run's own ZoneInfo lookup failed open to UTC at the NEXT
scheduler tick — degrading every daily@ job with no loud signal at
config-write time. The validator fails loud immediately instead.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.config.settings import SystemSettings


def test_default_is_utc() -> None:
    assert SystemSettings().timezone == "UTC"


def test_accepts_a_real_iana_zone() -> None:
    assert SystemSettings(timezone="America/New_York").timezone == "America/New_York"


def test_rejects_unresolvable_zone() -> None:
    with pytest.raises(ValidationError):
        SystemSettings(timezone="Not/AZone")


def test_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        SystemSettings(timezone="")
