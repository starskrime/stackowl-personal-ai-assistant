"""WS-A — resolve_owner_addresses (moved into notifications.recipient).

The owner→native-target resolver used to live in ``scheduler.assembly`` as
``_resolve_owner_addresses``. WS-A lifts it into ``notifications.recipient`` so
every producer path can share ONE resolver next to :class:`DeliverySpec`. These
tests pin the behavior (telegram single-owner → durable address; 0 / >1 / non-
telegram → no address) AND that the scheduler.assembly call site delegates to the
shared function with an IDENTICAL result.
"""

from __future__ import annotations

from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.settings import Settings
from stackowl.notifications.recipient import resolve_owner_addresses
from stackowl.scheduler.assembly import _resolve_owner_addresses


def _settings(allowed: list[int]) -> Settings:
    # Construct the nested TelegramSettings explicitly and inject it so the
    # allowlist under test is not overridden by env vars / a config file that a
    # BaseSettings load would otherwise pull in.
    settings = Settings()
    return settings.model_copy(
        update={
            "telegram_channel": TelegramSettings(
                allowed_user_ids=frozenset(allowed)
            )
        }
    )


def test_telegram_single_owner_resolves_to_chat_id() -> None:
    settings = _settings([12345])
    assert resolve_owner_addresses(settings, ["telegram"]) == {"telegram": 12345}


def test_telegram_zero_owners_yields_no_address() -> None:
    settings = _settings([])
    assert resolve_owner_addresses(settings, ["telegram"]) == {}


def test_telegram_multiple_owners_yields_no_address() -> None:
    settings = _settings([12345, 67890])
    assert resolve_owner_addresses(settings, ["telegram"]) == {}


def test_non_telegram_channel_yields_no_durable_token() -> None:
    settings = _settings([12345])
    assert resolve_owner_addresses(settings, ["slack"]) == {}


def test_assembly_wrapper_matches_shared_resolver() -> None:
    """The scheduler.assembly call site must produce an identical result."""
    for allowed, channels in (
        ([12345], ["telegram"]),
        ([], ["telegram"]),
        ([12345, 67890], ["telegram"]),
        ([12345], ["slack"]),
    ):
        settings = _settings(allowed)
        assert _resolve_owner_addresses(settings, channels) == resolve_owner_addresses(
            settings, channels
        )
