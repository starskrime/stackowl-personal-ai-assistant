"""The kit's own test Telegram bot (AD-16, AD-37): the token-collision guard
against the PLATFORM's config, and setup-code/device-request delivery.
Never calls the real Telegram Bot API -- every test here stubs the
`Application`/`Bot` layer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from bridge_spike import telegram_bot


# -- token-collision guard -----------------------------------------------


def test_no_platform_config_file_means_no_collision(tmp_path) -> None:
    missing_config = tmp_path / "does-not-exist.yaml"
    assert telegram_bot.check_token_collision("kit-token", config_path=missing_config) is None


def test_no_telegram_channel_configured_means_no_collision(tmp_path) -> None:
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"discord_channel": {"bot_token": "unrelated"}}))
    assert telegram_bot.check_token_collision("kit-token", config_path=config_path) is None


def test_collision_with_a_bare_env_var_reference(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_TEST_BOT_TOKEN", "same-secret-token")
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"telegram_channel": {"bot_token": "PLATFORM_TEST_BOT_TOKEN"}}))

    remedy = telegram_bot.check_token_collision("same-secret-token", config_path=config_path)
    assert remedy is not None
    assert telegram_bot.TEST_BOT_TOKEN_ENV in remedy


def test_no_collision_when_env_var_reference_resolves_to_a_different_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_TEST_BOT_TOKEN", "platforms-own-token")
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"telegram_channel": {"bot_token": "PLATFORM_TEST_BOT_TOKEN"}}))

    assert telegram_bot.check_token_collision("kits-own-token", config_path=config_path) is None


def test_collision_with_a_file_reference(tmp_path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("same-secret-token\n")
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"telegram_channel": {"bot_token": f"file:{secret_file}"}}))

    remedy = telegram_bot.check_token_collision("same-secret-token", config_path=config_path)
    assert remedy is not None


def test_collision_with_a_keychain_reference(tmp_path, monkeypatch) -> None:
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, username: "same-secret-token")
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"telegram_channel": {"bot_token": "keychain:stackowl-telegram-token"}}))

    remedy = telegram_bot.check_token_collision("same-secret-token", config_path=config_path)
    assert remedy is not None


def test_keychain_resolution_failure_does_not_block_the_check(tmp_path, monkeypatch) -> None:
    import keyring

    def _raise(*_args, **_kwargs):
        raise RuntimeError("no keychain backend available in this environment")

    monkeypatch.setattr(keyring, "get_password", _raise)
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"telegram_channel": {"bot_token": "keychain:stackowl-telegram-token"}}))

    assert telegram_bot.check_token_collision("any-token", config_path=config_path) is None


def test_malformed_platform_config_does_not_block_the_check(tmp_path) -> None:
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text("not: valid: yaml: [[[")
    assert telegram_bot.check_token_collision("any-token", config_path=config_path) is None


def test_test_telegram_bot_refuses_to_construct_on_collision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_TEST_BOT_TOKEN", "same-secret-token")
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"telegram_channel": {"bot_token": "PLATFORM_TEST_BOT_TOKEN"}}))

    with pytest.raises(telegram_bot.BotTokenCollisionError):
        telegram_bot.TestTelegramBot("same-secret-token", [12345], config_path=config_path)


def test_allowed_user_ids_from_env_parses_a_comma_separated_list() -> None:
    assert telegram_bot.allowed_user_ids_from_env("111, 222,333") == [111, 222, 333]
    assert telegram_bot.allowed_user_ids_from_env(None) == []
    assert telegram_bot.allowed_user_ids_from_env("") == []


def test_allowed_user_ids_from_env_ignores_non_integer_entries() -> None:
    assert telegram_bot.allowed_user_ids_from_env("111,not-a-number,222") == [111, 222]


# -- bot behaviour (fully stubbed Application/Bot; no network) -----------


def _fake_application() -> MagicMock:
    application = MagicMock()
    application.bot = MagicMock()
    application.bot.send_message = AsyncMock()
    application.initialize = AsyncMock()
    application.start = AsyncMock()
    application.stop = AsyncMock()
    application.shutdown = AsyncMock()
    application.updater = MagicMock()
    application.updater.start_polling = AsyncMock()
    application.updater.stop = AsyncMock()
    application.add_handler = MagicMock()
    return application


def _bot(tmp_path, **kwargs) -> telegram_bot.TestTelegramBot:
    config_path = tmp_path / "no-platform-config.yaml"  # never written -> no collision possible
    return telegram_bot.TestTelegramBot("kit-test-token", config_path=config_path, application=_fake_application(), **kwargs)


async def test_send_setup_code_sends_when_exactly_one_user_is_configured(tmp_path) -> None:
    bot = _bot(tmp_path, allowed_user_ids=[555])
    sent = await bot.send_setup_code("ABCD1234")
    assert sent is True
    bot._application.bot.send_message.assert_awaited_once()
    _args, kwargs = bot._application.bot.send_message.call_args
    assert kwargs["chat_id"] == 555
    assert "ABCD1234" in kwargs["text"]


async def test_send_setup_code_is_terminal_only_when_zero_users_configured(tmp_path) -> None:
    bot = _bot(tmp_path, allowed_user_ids=[])
    sent = await bot.send_setup_code("ABCD1234")
    assert sent is False
    bot._application.bot.send_message.assert_not_awaited()


async def test_send_setup_code_is_terminal_only_when_several_users_configured(tmp_path) -> None:
    bot = _bot(tmp_path, allowed_user_ids=[1, 2])
    sent = await bot.send_setup_code("ABCD1234")
    assert sent is False
    bot._application.bot.send_message.assert_not_awaited()


async def test_send_device_request_includes_an_approve_button_with_the_request_id_and_code(tmp_path) -> None:
    bot = _bot(tmp_path, allowed_user_ids=[555])
    sent = await bot.send_device_request("second-device", "ABC123", "req-42")
    assert sent is True
    _args, kwargs = bot._application.bot.send_message.call_args
    keyboard = kwargs["reply_markup"]
    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == "approve:req-42:ABC123"


async def test_start_and_stop_drive_the_application_lifecycle(tmp_path) -> None:
    bot = _bot(tmp_path, allowed_user_ids=[555])
    await bot.start()
    bot._application.initialize.assert_awaited_once()
    bot._application.start.assert_awaited_once()
    bot._application.updater.start_polling.assert_awaited_once()

    await bot.stop()
    bot._application.updater.stop.assert_awaited_once()
    bot._application.stop.assert_awaited_once()
    bot._application.shutdown.assert_awaited_once()


async def test_stop_before_start_is_a_no_op(tmp_path) -> None:
    bot = _bot(tmp_path, allowed_user_ids=[555])
    await bot.stop()
    bot._application.stop.assert_not_awaited()


async def test_callback_query_approve_button_invokes_the_approve_callback(tmp_path) -> None:
    approve_callback = AsyncMock()
    bot = _bot(tmp_path, allowed_user_ids=[555], approve_callback=approve_callback)
    update = MagicMock()
    update.callback_query.data = "approve:req-42:ABC123"
    update.callback_query.answer = AsyncMock()

    await bot._on_callback_query(update, MagicMock())

    approve_callback.assert_awaited_once_with("req-42", "ABC123")
    update.callback_query.answer.assert_awaited_once()


async def test_callback_query_ignores_unrelated_callback_data(tmp_path) -> None:
    approve_callback = AsyncMock()
    bot = _bot(tmp_path, allowed_user_ids=[555], approve_callback=approve_callback)
    update = MagicMock()
    update.callback_query.data = "something-else"

    await bot._on_callback_query(update, MagicMock())

    approve_callback.assert_not_awaited()
