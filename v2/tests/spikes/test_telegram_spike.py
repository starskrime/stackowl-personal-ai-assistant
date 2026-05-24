"""Telegram library evaluation spike — Story 9.1.

Run with: ``STACKOWL_RUN_SPIKES=1 uv run pytest tests/spikes/ -m spike -v``
(or pass ``--runspike`` to pytest, which sets the env var for you).

This spike is SKIPPED in CI unless ``STACKOWL_RUN_SPIKES=1`` is set. The
decision has already been made — ``python-telegram-bot>=21.0`` — and is
documented in ``docs/adr-telegram-library.md``. This file is retained for
auditability and to assert that the ADR remains in the resolved state.

Scoring rationale (full matrix is in the ADR):

- ``python-telegram-bot 21``: 7/7 — async-native, Apache-2.0, polling+webhook,
  voice download via ``Bot.get_file``, inline keyboards, Python 3.11+.
- ``aiogram 3``: 7/7 — equivalent on the matrix; loses tie-break on
  ecosystem maturity and documentation depth.
- ``telethon 1.x``: 4/7 — MTProto (not Bot API), webhook unsupported for bots,
  divergent API surface for media/keyboards, non-OSI licence variant.

The tie-break in favour of ``python-telegram-bot`` rests on richer
documentation, mature ``JobQueue``/``ConversationHandler`` extensions, and a
larger community of recipes for production-grade bots. Both libraries remain
swappable via :class:`ChannelAdapter`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_ADR_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "adr-telegram-library.md"
)


pytestmark = [
    pytest.mark.spike,
    pytest.mark.skipif(
        os.environ.get("STACKOWL_RUN_SPIKES") != "1",
        reason="Spike tests skipped; set STACKOWL_RUN_SPIKES=1 to run.",
    ),
]


def test_telegram_library_spike_resolved() -> None:
    """The Story 9.1 ADR must exist and declare the spike resolved."""
    assert _ADR_PATH.exists(), f"ADR missing: {_ADR_PATH}"
    text = _ADR_PATH.read_text(encoding="utf-8")
    assert "Resolved (Story 9.1)" in text, (
        "ADR must declare 'Resolved (Story 9.1)' status."
    )
    assert "python-telegram-bot>=21.0" in text, (
        "ADR must record the chosen library version."
    )
