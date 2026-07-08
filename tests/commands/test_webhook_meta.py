"""/webhook sub-command metadata — declared subs match the dispatch ladder."""

from __future__ import annotations

import pytest

from stackowl.commands.webhook_command import WebhookCommand


def _state():  # type: ignore[no-untyped-def]
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def test_webhook_declares_real_subcommands() -> None:
    cmd = WebhookCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == {"register", "list", "enable", "disable"}
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Integrations"
    for sub in cmd.meta.subcommands:
        assert sub.summary


def test_source_subcommands_declare_source_arg() -> None:
    cmd = WebhookCommand()
    by_name = {s.name: s for s in cmd.meta.subcommands}
    for sub in ("register", "enable", "disable"):
        assert by_name[sub].args
        assert by_name[sub].args[0].name == "source"
    assert by_name["list"].args == ()


@pytest.mark.asyncio
async def test_empty_args_returns_usage_listing_subs() -> None:
    cmd = WebhookCommand()  # db/settings None → not configured branch
    out = await cmd.handle("", _state())
    # Not configured (no db) — but with a configured command, empty args
    # yields usage. Probe the usage path via a configured stub instead.
    assert out  # smoke

    cmd2 = WebhookCommand(db=_FakeDb(), settings=_FakeSettings())
    out2 = await cmd2.handle("", _state())
    assert "Usage" in out2
    assert "register" in out2 and "list" in out2 and "disable" in out2


class _FakeDb:
    async def fetch_all(self, sql: str, params):  # type: ignore[no-untyped-def]
        return []


class _FakeWebhook:
    sources: dict[str, object] = {}


class _FakeSettings:
    webhook = _FakeWebhook()
