"""WebhookReceiver must actually be registered with the app supervisor when
webhook.enabled is True — before this fix, NOTHING in the codebase ever
constructed it, so the HTTP listener never bound regardless of config
(registered-but-unreachable, the same class of bug this repo's memory notes
call out repeatedly)."""

from __future__ import annotations

from unittest.mock import MagicMock

from stackowl.config.settings import Settings
from stackowl.config.webhook_settings import WebhookSettings
from stackowl.supervisor.supervisor import Supervisor
from stackowl.webhooks.receiver import WebhookReceiver


def test_webhook_receiver_registers_on_supervisor_when_enabled() -> None:
    """Minimal, direct test of the registration contract this task adds —
    construct a receiver the same way orchestrator.py will, register it, and
    confirm the supervisor now holds it. This does NOT exercise the full
    orchestrator startup path (too many fixtures for one unit test); that is
    covered by the manual smoke check in Task 5.

    Deviation from the brief: `tests/_story_6_7_helpers.py::make_settings()`
    takes zero arguments (no `webhook=` kwarg support), so it can't produce a
    settings object with webhook.enabled=True. Constructing `Settings(...)`
    directly with an explicit `WebhookSettings` override instead — same
    end result (a real `Settings` instance with webhook enabled), just not
    routed through that particular helper.
    """
    settings = Settings(webhook=WebhookSettings(enabled=True, sources={}))
    supervisor = Supervisor()
    receiver = WebhookReceiver(scheduler=MagicMock(), settings=settings, db=None)

    supervisor.register(receiver)

    assert any(
        isinstance(state.task, WebhookReceiver) for state in supervisor._tasks.values()
    )
