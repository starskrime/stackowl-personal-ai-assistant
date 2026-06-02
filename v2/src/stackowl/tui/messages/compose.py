"""ComposeArea input messages — submissions and autocomplete selections."""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.tui.messages._base import FrozenMessage


# NOT frozen: this message bubbles from the ComposeArea child widget up to the
# App, and Textual's pump mutates internal bookkeeping (``_no_default_action``,
# ``_stop_propagation``) during bubbling — which a frozen dataclass rejects,
# crashing the pump.  Payload immutability is preserved by convention (set once
# at construction); the FrozenMessage base permits only the pump's writes.
@dataclass
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
