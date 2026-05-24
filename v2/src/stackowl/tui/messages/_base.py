"""Internal helper: frozen-dataclass-compatible Textual message base."""

from __future__ import annotations

from textual import message as _textual_message
from textual.message import Message


class FrozenMessage(Message):
    """Textual Message subclass safe to compose with ``@dataclass(frozen=True)``.

    Textual's stock ``__post_init__`` writes ``_sender`` etc. directly, which
    a frozen dataclass disallows.  This override uses :func:`object.__setattr__`
    so the user-facing payload fields remain immutable while internal Textual
    bookkeeping still gets initialized.
    """

    def __post_init__(self) -> None:
        active_pump = _textual_message.active_message_pump  # type: ignore[attr-defined]
        time_mod = _textual_message._time  # type: ignore[attr-defined]
        object.__setattr__(self, "_sender", active_pump.get(None))
        object.__setattr__(self, "time", time_mod.get_time())
        object.__setattr__(self, "_forwarded", False)
        object.__setattr__(self, "_no_default_action", False)
        object.__setattr__(self, "_stop_propagation", False)
        object.__setattr__(self, "_prevent", set())
