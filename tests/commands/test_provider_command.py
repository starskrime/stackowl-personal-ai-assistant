"""Tests for /provider slash command + shared store_secret helper.

Covers list/add/remove/set-tier, secret-ref storage (never the raw token),
validation errors, settings_reloaded emission, and the keychain/file fallback
of the shared secret writer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from stackowl.events.bus import EventBus
from stackowl.pipeline.state import PipelineState

RAW_TOKEN = "sk-super-secret-RAW-TOKEN-12345"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _state(session: str = "sess-1") -> PipelineState:
    return PipelineState(
        trace_id="trace-1",
        session_id=session,
        input_text="hello",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


class _SpyBus(EventBus):
    """EventBus that records every emit for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, Any]] = []

    def emit(self, event: str, payload: Any = None) -> None:  # type: ignore[override]
        self.events.append((event, payload))
        super().emit(event, payload)


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump({"test_mode": True, "providers": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


def _make_cmd(bus: EventBus | None = None) -> Any:
    from stackowl.commands.provider_command import ProviderCommand

    return ProviderCommand(event_bus=bus)


def _load(cfg: Path) -> dict[str, Any]:
    return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestProviderList:
    @pytest.mark.asyncio
    async def test_list_no_providers(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("list", _state())
        assert "No providers" in out or "no providers" in out.lower()

    @pytest.mark.asyncio
    async def test_empty_args_acts_as_list(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("", _state())
        assert "no providers" in out.lower()

    @pytest.mark.asyncio
    async def test_list_shows_providers_without_raw_token(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [
            {
                "name": "acme",
                "protocol": "openai",
                "default_model": "gpt-x",
                "tier": "fast",
                "enabled": True,
                "api_key": "keychain:stackowl-provider-acme",
            }
        ]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("list", _state())
        assert "acme" in out
        assert "openai" in out
        assert "gpt-x" in out
        assert "fast" in out
        # The REF may be shown; the raw secret must NEVER appear.
        assert "keychain:stackowl-provider-acme" in out
        assert RAW_TOKEN not in out


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


class TestProviderAdd:
    @pytest.mark.asyncio
    async def test_add_valid_no_token(self, tmp_yaml: Path) -> None:
        bus = _SpyBus()
        out = await _make_cmd(bus).handle("add acme openai gpt-x fast", _state())
        assert "✓" in out or "added" in out.lower()
        data = _load(tmp_yaml)
        names = [p["name"] for p in data["providers"]]
        assert "acme" in names
        entry = next(p for p in data["providers"] if p["name"] == "acme")
        assert entry["protocol"] == "openai"
        assert entry["default_model"] == "gpt-x"
        assert entry["tier"] == "fast"
        assert entry.get("api_key") in (None, "")
        assert ("settings_reloaded", {"provider": "acme"}) in [
            (e, p if isinstance(p, dict) else p) for e, p in bus.events
        ] or any(e == "settings_reloaded" for e, _ in bus.events)

    @pytest.mark.asyncio
    async def test_add_with_token_stores_ref_not_raw(
        self, tmp_yaml: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force keyring path so we get a deterministic keychain: ref.
        import stackowl.config.secret_writer as sw

        captured: dict[str, str] = {}

        class _FakeKeyring:
            @staticmethod
            def set_password(service: str, user: str, secret: str) -> None:
                captured["service"] = service
                captured["secret"] = secret

        monkeypatch.setitem(__import__("sys").modules, "keyring", _FakeKeyring)

        out = await _make_cmd(_SpyBus()).handle(
            f"add acme openai gpt-x fast https://api.acme.test/v1 token={RAW_TOKEN}",
            _state(),
        )
        # Raw token never leaks into the response.
        assert RAW_TOKEN not in out
        assert "✓" in out or "added" in out.lower()

        data = _load(tmp_yaml)
        entry = next(p for p in data["providers"] if p["name"] == "acme")
        assert entry["base_url"] == "https://api.acme.test/v1"
        # api_key holds a REF, never the raw token.
        assert entry["api_key"] == "keychain:stackowl-provider-acme"
        assert entry["api_key"] != RAW_TOKEN
        # Raw token absent from the whole yaml file.
        assert RAW_TOKEN not in tmp_yaml.read_text(encoding="utf-8")
        # Secret writer actually received the raw token.
        assert captured["secret"] == RAW_TOKEN
        assert captured["service"] == "stackowl-provider-acme"
        # sw import kept for namespace sanity
        assert sw.store_secret is not None

    @pytest.mark.asyncio
    async def test_add_invalid_protocol(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("add acme telnet gpt-x fast", _state())
        assert "✗" in out or "error" in out.lower() or "invalid" in out.lower()
        assert _load(tmp_yaml)["providers"] == []

    @pytest.mark.asyncio
    async def test_add_invalid_tier(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("add acme openai gpt-x turbo", _state())
        assert "✗" in out or "invalid" in out.lower() or "error" in out.lower()
        assert _load(tmp_yaml)["providers"] == []

    @pytest.mark.asyncio
    async def test_add_duplicate_name(self, tmp_yaml: Path) -> None:
        await _make_cmd().handle("add acme openai gpt-x fast", _state())
        out = await _make_cmd().handle("add acme anthropic claude standard", _state())
        assert "✗" in out or "exists" in out.lower() or "duplicate" in out.lower()
        # Still only one acme; unchanged protocol.
        data = _load(tmp_yaml)
        acmes = [p for p in data["providers"] if p["name"] == "acme"]
        assert len(acmes) == 1
        assert acmes[0]["protocol"] == "openai"

    @pytest.mark.asyncio
    async def test_add_missing_args_returns_usage(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("add acme openai", _state())
        assert "Usage" in out or "usage" in out.lower()


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestProviderRemove:
    @pytest.mark.asyncio
    async def test_remove_existing(self, tmp_yaml: Path) -> None:
        bus = _SpyBus()
        await _make_cmd().handle("add acme openai gpt-x fast", _state())
        out = await _make_cmd(bus).handle("remove acme", _state())
        assert "✓" in out or "removed" in out.lower()
        assert _load(tmp_yaml)["providers"] == []
        assert any(e == "settings_reloaded" for e, _ in bus.events)

    @pytest.mark.asyncio
    async def test_remove_missing(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("remove ghost", _state())
        assert "✗" in out or "not found" in out.lower()

    @pytest.mark.asyncio
    async def test_remove_no_name_usage(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("remove", _state())
        assert "Usage" in out or "usage" in out.lower()


# ---------------------------------------------------------------------------
# set-tier
# ---------------------------------------------------------------------------


class TestProviderSetTier:
    @pytest.mark.asyncio
    async def test_set_tier_valid(self, tmp_yaml: Path) -> None:
        bus = _SpyBus()
        await _make_cmd().handle("add acme openai gpt-x fast", _state())
        out = await _make_cmd(bus).handle("set-tier acme powerful", _state())
        assert "✓" in out or "powerful" in out
        entry = next(p for p in _load(tmp_yaml)["providers"] if p["name"] == "acme")
        assert entry["tier"] == "powerful"
        assert any(e == "settings_reloaded" for e, _ in bus.events)

    @pytest.mark.asyncio
    async def test_set_tier_invalid(self, tmp_yaml: Path) -> None:
        await _make_cmd().handle("add acme openai gpt-x fast", _state())
        out = await _make_cmd().handle("set-tier acme turbo", _state())
        assert "✗" in out or "invalid" in out.lower()
        entry = next(p for p in _load(tmp_yaml)["providers"] if p["name"] == "acme")
        assert entry["tier"] == "fast"

    @pytest.mark.asyncio
    async def test_set_tier_missing_provider(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("set-tier ghost powerful", _state())
        assert "✗" in out or "not found" in out.lower()

    @pytest.mark.asyncio
    async def test_set_tier_usage(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("set-tier acme", _state())
        assert "Usage" in out or "usage" in out.lower()


# ---------------------------------------------------------------------------
# usage / unknown subcommand
# ---------------------------------------------------------------------------


class TestProviderUsage:
    @pytest.mark.asyncio
    async def test_unknown_subcommand(self, tmp_yaml: Path) -> None:
        out = await _make_cmd().handle("frobnicate", _state())
        assert "Usage" in out or "usage" in out.lower()

    @pytest.mark.asyncio
    async def test_command_metadata(self) -> None:
        cmd = _make_cmd()
        assert cmd.command == "provider"
        assert isinstance(cmd.description, str) and cmd.description


# ---------------------------------------------------------------------------
# Registration / discovery
# ---------------------------------------------------------------------------


class TestProviderRegistration:
    def test_registered_via_assembly(self) -> None:
        # /provider is a DI command (Epic C1) — it registers via register_all_commands,
        # not via Pattern-A self-registration.  load_builtin_commands alone is not enough.
        from stackowl.commands.assembly import CommandDeps, register_all_commands
        from stackowl.commands.registry import CommandRegistry

        CommandRegistry.reset()
        register_all_commands(CommandDeps(), registry=CommandRegistry.instance())
        names = [c.command for c in CommandRegistry.instance().list()]
        assert "provider" in names


class TestNoTokenLeakInLogs:
    """The raw token must never reach the logs — including the shared dispatcher,
    which previously logged the raw args string (and thus the token)."""

    async def test_token_absent_from_logs_via_dispatch(
        self, tmp_yaml: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from stackowl.commands.provider_command import ProviderCommand
        from stackowl.commands.registry import CommandRegistry

        reg = CommandRegistry()
        reg.register(ProviderCommand())
        with caplog.at_level(logging.DEBUG):
            out = await reg.dispatch(
                "provider", f"add acme openai gpt-x fast token={RAW_TOKEN}", _state()
            )
        blob = "\n".join(
            r.getMessage() + str(getattr(r, "_fields", "")) for r in caplog.records
        )
        assert RAW_TOKEN not in blob, "raw token leaked into a log record"
        assert RAW_TOKEN not in out, "raw token echoed in the command response"
        # And it landed in the yaml only as a resolver ref, not raw.
        assert RAW_TOKEN not in tmp_yaml.read_text(encoding="utf-8")
