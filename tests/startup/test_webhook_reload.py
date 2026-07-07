"""Mirrors tests/startup/test_provider_reload.py's shape for the analogous
webhook subscriber — type-guards on Settings, ignores dict payloads, never
raises out of the handler."""
from __future__ import annotations

from unittest.mock import MagicMock

from stackowl.startup.webhook_reload import make_webhook_reload_handler


def test_webhook_reload_applies_settings_payload():
    receiver = MagicMock()
    handler = make_webhook_reload_handler(receiver)

    from stackowl.config.settings import Settings
    settings = Settings()
    handler(settings)

    receiver.apply_settings.assert_called_once_with(settings)


def test_webhook_reload_ignores_dict_payload():
    receiver = MagicMock()
    handler = make_webhook_reload_handler(receiver)

    handler({"source": "acme"})

    receiver.apply_settings.assert_not_called()


def test_webhook_reload_never_raises_on_apply_error():
    receiver = MagicMock()
    receiver.apply_settings.side_effect = RuntimeError("boom")
    handler = make_webhook_reload_handler(receiver)

    from stackowl.config.settings import Settings
    handler(Settings())  # must not raise
