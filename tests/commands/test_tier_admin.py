"""Tests for /tier's provider-tier membership admin job (list/menu/add/remove).

The session-preference job (bare tier name) is covered by
tests/journeys/commands/test_tier_command.py and is untouched by this file —
these tests are scoped to the NEW admin subcommands merged in alongside it
(see tier_command.py's module docstring for why the two share one command).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from stackowl.commands.response import CommandResponse
from stackowl.commands.tier_command import reset_session_tiers
from stackowl.pipeline.state import PipelineState


@pytest.fixture(autouse=True)
def _reset_session_tier_cache() -> None:
    """The preference job's in-memory cache is module-level global state,
    keyed by session_id — without a reset, a preference set by one test
    (e.g. "fast" for the shared _state() session id) leaks into every test
    that runs after it in the same process, including the admin tests below
    that don't touch preferences at all but share the same default session."""
    reset_session_tiers()

# ---------------------------------------------------------------------------
# Fixtures
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


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "test_mode": True,
                "providers": [
                    {
                        "name": "groq",
                        "protocol": "openai",
                        "default_model": "llama-3.3-70b-versatile",
                        "tier": "fast",
                        "enabled": True,
                    },
                    {
                        "name": "openai",
                        "protocol": "openai",
                        "default_model": "gpt-4o",
                        "tier": "powerful",
                        "enabled": True,
                    },
                    {
                        "name": "disabled-one",
                        "protocol": "openai",
                        "default_model": "m",
                        "tier": "fast",
                        "enabled": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


def _make_cmd(registry: Any = None) -> Any:
    from stackowl.commands.tier_command import TierCommand

    return TierCommand(event_bus=None, registry=registry)


def _load(cfg: Path) -> dict[str, Any]:
    return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# list — dashboard
# ---------------------------------------------------------------------------


class TestTierAdminList:
    @pytest.mark.asyncio
    async def test_list_shows_all_tiers_with_members(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("list", _state())
        assert isinstance(reply, CommandResponse)
        assert "groq" in reply.text
        assert "openai" in reply.text
        assert "disabled-one" in reply.text
        assert "(disabled)" in reply.text
        # One "Manage <tier>" button per tier (4 tiers).
        manage_commands = [a.command for a in reply.actions if a.command.startswith("/tier menu")]
        assert len(manage_commands) == 4

    @pytest.mark.asyncio
    async def test_list_no_stackowl_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(tmp_path / "missing.yaml"))
        reply = await _make_cmd().handle("list", _state())
        assert isinstance(reply, str)
        assert "No stackowl.yaml" in reply


# ---------------------------------------------------------------------------
# menu — per-tier drill-down
# ---------------------------------------------------------------------------


class TestTierAdminMenu:
    @pytest.mark.asyncio
    async def test_menu_shows_members_and_remove_buttons(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("menu fast", _state())
        assert isinstance(reply, CommandResponse)
        assert "groq" in reply.text
        assert "disabled-one" in reply.text
        assert any(a.command == "/tier remove fast groq" for a in reply.actions)
        assert any(a.command == "/tier add fast" for a in reply.actions)
        assert any(a.command == "/tier list" for a in reply.actions)

    @pytest.mark.asyncio
    async def test_menu_empty_tier(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("menu local", _state())
        assert isinstance(reply, CommandResponse)
        assert "no providers" in reply.text.lower()

    @pytest.mark.asyncio
    async def test_menu_invalid_tier(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("menu nonexistent", _state())
        assert isinstance(reply, str)
        assert "Usage" in reply

    @pytest.mark.asyncio
    async def test_menu_no_tier_arg(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("menu", _state())
        assert isinstance(reply, str)
        assert "Usage" in reply


# ---------------------------------------------------------------------------
# add — browse + execute
# ---------------------------------------------------------------------------


class TestTierAdminAdd:
    @pytest.mark.asyncio
    async def test_add_browse_shows_candidates_from_other_tiers(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add powerful", _state())
        assert isinstance(reply, CommandResponse)
        # groq (fast) and disabled-one (fast) are candidates; openai is already powerful.
        assert any(a.command == "/tier add powerful groq" for a in reply.actions)
        assert not any("openai" in a.command for a in reply.actions)

    @pytest.mark.asyncio
    async def test_add_execute_moves_provider_to_tier(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add powerful groq", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        assert "powerful" in reply
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        assert groq["tier"] == "powerful"

    @pytest.mark.asyncio
    async def test_add_execute_already_in_tier(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add fast groq", _state())
        assert isinstance(reply, str)
        assert "already in tier" in reply

    @pytest.mark.asyncio
    async def test_add_execute_provider_not_found(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add fast ghost", _state())
        assert isinstance(reply, str)
        assert "✗" in reply
        assert "not found" in reply

    @pytest.mark.asyncio
    async def test_add_execute_invalid_tier(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add nonexistent groq", _state())
        assert isinstance(reply, str)
        assert "Invalid tier" in reply

    @pytest.mark.asyncio
    async def test_add_browse_all_already_in_tier_offers_provider_add(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "stackowl.yaml"
        cfg.write_text(
            yaml.dump({"test_mode": True, "providers": [
                {"name": "solo", "protocol": "openai", "default_model": "m", "tier": "fast", "enabled": True},
            ]}),
            encoding="utf-8",
        )
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
        reply = await _make_cmd().handle("add fast", _state())
        assert isinstance(reply, CommandResponse)
        assert any(a.command == "/provider add" for a in reply.actions)


# ---------------------------------------------------------------------------
# remove — browse + execute
# ---------------------------------------------------------------------------


class TestTierAdminRemove:
    @pytest.mark.asyncio
    async def test_remove_browse_shows_only_enabled_candidates(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("remove fast", _state())
        assert isinstance(reply, CommandResponse)
        commands = [a.command for a in reply.actions]
        assert "/tier remove fast groq" in commands
        # disabled-one is already disabled — not offered again.
        assert "/tier remove fast disabled-one" not in commands

    @pytest.mark.asyncio
    async def test_remove_execute_disables_provider(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("remove fast groq", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        assert "disabled" in reply
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        assert groq["enabled"] is False
        # Tier is preserved (reversible via /provider enable).
        assert groq["tier"] == "fast"

    @pytest.mark.asyncio
    async def test_remove_execute_wrong_tier_rejected(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("remove powerful groq", _state())
        assert isinstance(reply, str)
        assert "✗" in reply
        assert "not in tier" in reply
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        assert groq["enabled"] is True  # untouched

    @pytest.mark.asyncio
    async def test_remove_execute_provider_not_found(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("remove fast ghost", _state())
        assert isinstance(reply, str)
        assert "not found" in reply

    @pytest.mark.asyncio
    async def test_remove_browse_no_active_candidates(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("remove local", _state())
        assert isinstance(reply, str)
        assert "No active providers" in reply


# ---------------------------------------------------------------------------
# preference job still works after the merge (regression guard)
# ---------------------------------------------------------------------------


class TestTierPreferenceStillWorksAfterMerge:
    @pytest.mark.asyncio
    async def test_bare_tier_name_still_sets_preference(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("fast", _state())
        assert isinstance(reply, str)
        assert "preference set to fast" in reply.lower()

    @pytest.mark.asyncio
    async def test_unknown_token_rejected_with_usage(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("ultra", _state())
        assert isinstance(reply, str)
        assert "✗" in reply
        assert "Usage" in reply

    @pytest.mark.asyncio
    async def test_bare_tier_no_args_shows_preference_text_with_buttons(
        self, tmp_yaml: Path
    ) -> None:
        """Regression guard for the reported gap: bare /tier used to be a
        dead-end plain-text reply with zero buttons, undiscoverable from the
        new admin dashboard. Text wording must stay unchanged; buttons are new."""
        reply = await _make_cmd().handle("", _state())
        assert isinstance(reply, CommandResponse)
        assert reply.text == "Current tier preference: default\nValid tiers: fast, standard, powerful, local"
        commands = [a.command for a in reply.actions]
        assert "/tier fast" in commands
        assert "/tier standard" in commands
        assert "/tier powerful" in commands
        assert "/tier local" in commands
        assert "/tier list" in commands


# ---------------------------------------------------------------------------
# DI registration
# ---------------------------------------------------------------------------


class TestTierRegistration:
    def test_registered_via_assembly(self) -> None:
        # /tier is now a DI command (mirrors /provider) — not Pattern-A.
        from stackowl.commands.assembly import CommandDeps, register_all_commands
        from stackowl.commands.registry import CommandRegistry

        CommandRegistry.reset()
        register_all_commands(CommandDeps(), registry=CommandRegistry.instance())
        names = [c.command for c in CommandRegistry.instance().list()]
        assert "tier" in names

    def test_wired_to_the_same_provider_registry_as_provider_command(self) -> None:
        from stackowl.commands.assembly import CommandDeps, register_all_commands
        from stackowl.commands.registry import CommandRegistry

        sentinel = object()
        CommandRegistry.reset()
        register_all_commands(
            CommandDeps(provider_registry=sentinel), registry=CommandRegistry.instance()
        )
        tier_cmd = CommandRegistry.instance().get("tier")
        provider_cmd = CommandRegistry.instance().get("provider")
        assert tier_cmd is not None and provider_cmd is not None
        assert tier_cmd._registry is sentinel  # type: ignore[attr-defined]
        assert tier_cmd._registry is provider_cmd._registry  # type: ignore[attr-defined]
