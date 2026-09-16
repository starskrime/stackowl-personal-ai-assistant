"""Kit-level Telegram-collision safety wiring: `kit.py::_serve` must catch
`telegram_bot.BotTokenCollisionError` from `_start_test_telegram_bot`, print
a remedy, and exit 1 -- BEFORE ever binding the real HTTPS listener.
`test_telegram_bot.py` covers `TestTelegramBot`'s own collision check in
isolation; this covers the wiring one level up, so a regression that
dropped or mis-wired that `except` block in `_serve` would still be caught.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import kit
import yaml
from bridge_spike import mdns
from bridge_spike import telegram_bot
from bridge_spike.server import BridgeServer


def _wire_up_a_colliding_platform_config(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "stackowl.yaml"
    config_path.write_text(yaml.safe_dump({"telegram_channel": {"bot_token": "PLATFORM_TOKEN_VAR"}}))
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("PLATFORM_TOKEN_VAR", "same-secret-token")
    monkeypatch.setenv(telegram_bot.TEST_BOT_TOKEN_ENV, "same-secret-token")
    monkeypatch.setenv(telegram_bot.TEST_ALLOWED_USER_IDS_ENV, "555")

    # Never touch the real mDNS tooling or bind a real listener from a
    # unit test -- the collision must be caught before either matters.
    monkeypatch.setattr(mdns, "start_advertising", lambda *a, **k: mdns.Advertisement(command=[], process=None))
    monkeypatch.setattr(kit, "RESULTS_DIR", tmp_path)


async def test_serve_exits_1_on_bot_token_collision_without_starting_the_server(tmp_path, monkeypatch) -> None:
    _wire_up_a_colliding_platform_config(tmp_path, monkeypatch)
    start_mock = AsyncMock()
    monkeypatch.setattr(BridgeServer, "start", start_mock)

    exit_code = await kit._serve("kit-collision-serve-test.local", 8443, renewing=False)

    assert exit_code == 1
    start_mock.assert_not_awaited()


def test_cmd_start_returns_1_on_bot_token_collision_without_starting_the_server(tmp_path, monkeypatch) -> None:
    _wire_up_a_colliding_platform_config(tmp_path, monkeypatch)
    start_mock = AsyncMock()
    monkeypatch.setattr(BridgeServer, "start", start_mock)

    args = kit.build_parser().parse_args(["start", "--name", "kit-collision-cmd-test.local"])
    exit_code = kit.cmd_start(args)

    assert exit_code == 1
    start_mock.assert_not_awaited()
