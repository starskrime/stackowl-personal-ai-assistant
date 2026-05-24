"""InlineKeyboardBuilder — fluent builder for Telegram inline keyboard dicts.

Telegram inline keyboards are represented as JSON-serialisable dicts with the
shape ``{"inline_keyboard": [[{"text": ..., "callback_data": ...}, ...], ...]}``.
:class:`InlineKeyboardBuilder` provides a fluent API to assemble these safely,
enforcing Telegram's 64-byte ``callback_data`` limit at build time.

All user-facing button labels are sourced via :func:`~stackowl.tui.i18n.localize`
so the platform stays multilingual.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

__all__ = ["InlineKeyboardBuilder"]

_MAX_CALLBACK_DATA_LEN = 64  # Telegram hard limit


class InlineKeyboardBuilder:
    """Fluent builder for Telegram inline keyboard reply-markup dicts.

    Usage::

        kb = (
            InlineKeyboardBuilder()
            .add_button("Yes", "action:yes")
            .add_button("No", "action:no")
            .add_row()
            .add_button("Cancel", "action:cancel")
            .build()
        )

    Telegram limits ``callback_data`` to 64 bytes; :meth:`add_button` raises
    :exc:`ValueError` if this constraint would be violated.
    """

    def __init__(self) -> None:
        # Each element is one row (list of button dicts).
        self._rows: list[list[dict[str, str]]] = [[]]
        log.telegram.debug("[telegram] keyboard.builder.init: entry")

    def add_button(self, text: str, callback_data: str) -> "InlineKeyboardBuilder":
        """Append a button to the current row.

        4-point logging: entry / decision / step / exit.

        Args:
            text: Button label shown to the user.
            callback_data: Opaque string sent back when the user taps the button.

        Returns:
            ``self`` for chaining.

        Raises:
            ValueError: If ``callback_data`` exceeds 64 characters.
        """
        log.telegram.debug(
            "[telegram] keyboard.builder.add_button: entry",
            extra={"_fields": {"text_len": len(text), "data_len": len(callback_data)}},
        )
        if len(callback_data) > _MAX_CALLBACK_DATA_LEN:
            raise ValueError(
                f"callback_data must be ≤{_MAX_CALLBACK_DATA_LEN} chars, "
                f"got {len(callback_data)}: {callback_data!r}"
            )
        log.telegram.debug(
            "[telegram] keyboard.builder.add_button: decision data_valid",
            extra={"_fields": {"data_len": len(callback_data)}},
        )
        self._rows[-1].append({"text": text, "callback_data": callback_data})
        log.telegram.debug(
            "[telegram] keyboard.builder.add_button: exit",
            extra={"_fields": {"row_index": len(self._rows) - 1}},
        )
        return self

    def add_row(self) -> "InlineKeyboardBuilder":
        """Start a new button row.

        Returns:
            ``self`` for chaining.
        """
        log.telegram.debug("[telegram] keyboard.builder.add_row: entry")
        self._rows.append([])
        log.telegram.debug(
            "[telegram] keyboard.builder.add_row: exit",
            extra={"_fields": {"row_count": len(self._rows)}},
        )
        return self

    def build(self) -> dict[str, object]:
        """Serialise the keyboard into a Telegram-compatible dict.

        4-point logging: entry / decision / step / exit.

        Returns:
            ``{"inline_keyboard": [[{"text": …, "callback_data": …}, …], …]}``.
            Empty trailing rows are excluded.
        """
        log.telegram.debug("[telegram] keyboard.builder.build: entry")
        # Exclude trailing empty rows produced by a final add_row() call.
        rows = [row for row in self._rows if row]
        log.telegram.debug(
            "[telegram] keyboard.builder.build: decision filter_empty_rows",
            extra={"_fields": {"row_count": len(rows)}},
        )
        result: dict[str, object] = {"inline_keyboard": rows}
        log.telegram.debug(
            "[telegram] keyboard.builder.build: exit",
            extra={"_fields": {"row_count": len(rows)}},
        )
        return result

    @classmethod
    def from_memory_fact(cls, fact_id: str, lang: str = "en") -> dict[str, object]:
        """Build an approve/reject keyboard for a memory suggestion nudge.

        4-point logging: entry / decision / step / exit.

        Args:
            fact_id: Stable fact identifier; embedded in ``callback_data``.
            lang: BCP-47 language tag for button labels.

        Returns:
            Telegram inline keyboard dict with two buttons in one row.
        """
        log.telegram.debug(
            "[telegram] keyboard.builder.from_memory_fact: entry",
            extra={"_fields": {"fact_id": fact_id, "lang": lang}},
        )
        approve_label = localize("telegram.memory.approve", lang)
        reject_label = localize("telegram.memory.reject", lang)
        log.telegram.debug(
            "[telegram] keyboard.builder.from_memory_fact: decision labels_resolved",
            extra={"_fields": {"lang": lang}},
        )
        result = (
            cls()
            .add_button(approve_label, f"mem:approve:{fact_id}")
            .add_button(reject_label, f"mem:reject:{fact_id}")
            .build()
        )
        log.telegram.debug(
            "[telegram] keyboard.builder.from_memory_fact: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )
        return result

    @classmethod
    def from_confirmation(
        cls, action: str, target_id: str, lang: str = "en"
    ) -> dict[str, object]:
        """Build a yes/no confirmation keyboard.

        4-point logging: entry / decision / step / exit.

        Args:
            action: Short action name; combined with ``target_id`` in callback.
            target_id: Identifies the entity being confirmed.
            lang: BCP-47 language tag for button labels.

        Returns:
            Telegram inline keyboard dict with yes and no buttons.
        """
        log.telegram.debug(
            "[telegram] keyboard.builder.from_confirmation: entry",
            extra={"_fields": {"action": action, "lang": lang}},
        )
        yes_label = localize("telegram.confirm.yes", lang)
        no_label = localize("telegram.confirm.no", lang)
        log.telegram.debug(
            "[telegram] keyboard.builder.from_confirmation: decision labels_resolved",
            extra={"_fields": {"lang": lang}},
        )
        result = (
            cls()
            .add_button(yes_label, f"confirm:{action}:{target_id}:yes")
            .add_button(no_label, f"confirm:{action}:{target_id}:no")
            .build()
        )
        log.telegram.debug(
            "[telegram] keyboard.builder.from_confirmation: exit",
            extra={"_fields": {"action": action, "target_id_len": len(target_id)}},
        )
        return result
