"""ComposeArea input messages — submissions and autocomplete selections."""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.tui.messages._base import FrozenMessage


@dataclass(frozen=True)
class ComposeSubmittedMessage(FrozenMessage):
    """Emitted when the user submits a composed line.

    Attributes:
        text: Raw user input, already stripped of trailing whitespace.  All
            routing decisions (slash command vs. owl mention vs. parliament)
            happen downstream in the gateway scanner.
    """

    text: str


@dataclass(frozen=True)
class AutocompleteSelectedMessage(FrozenMessage):
    """Emitted when the user picks an entry from the autocomplete dropdown.

    Attributes:
        selected: The text that was accepted (without leading ``/`` or ``@``).
        completion_type: Either ``"command"`` (slash command) or ``"owl"``
            (``@``-mention).  Drives how the consumer re-inserts the value.
    """

    selected: str
    completion_type: str
