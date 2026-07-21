"""Dispatch tests — /provider is wired through CommandRegistry with a live bus.

Epic C1: proves the dead-bus regression is fixed for /provider.

NOTE: a unit-level test suite already lives at tests/commands/test_provider_command.py
(covers list/add/remove/set-tier exhaustively, driving the command directly).
These journey tests focus on REGISTRY WIRING and BUS EMISSION — the two things
the unit tests cannot catch (they construct ProviderCommand with a self-injected
_SpyBus, bypassing the DI path entirely).

Anti-mock rules (enforced by test_no_mock_only_command_tests.py):
  - Drive via CommandRegistry.dispatch, never call the command method directly.
  - Do NOT construct a real EventBus — use MagicMock or a fake instead.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump({"test_mode": True, "providers": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


async def test_provider_reachable_via_assembly(tmp_yaml: Path) -> None:
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (await CommandRegistry.instance().dispatch("provider", "list", make_state())).text
    assert isinstance(result, str) and result


async def test_provider_not_found_without_registration() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("provider", "list", make_state())


# ---------------------------------------------------------------------------
# Bus wiring — the production bus must fire (catches the bus=None regression)
# ---------------------------------------------------------------------------


async def test_provider_add_emits_on_production_bus(tmp_yaml: Path) -> None:
    """The event_bus in CommandDeps must receive settings_reloaded after /provider add.

    Regression gate: with Pattern-A the command was self-registered with
    event_bus=None, so _emit_reloaded was always a no-op.
    """
    from stackowl.config.settings import Settings

    spy = MagicMock()

    deps = CommandDeps(event_bus=spy)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = (
        await CommandRegistry.instance().dispatch(
            "provider", "add acme openai gpt-x fast", make_state()
        )
    ).text

    assert "✓" in result or "added" in result.lower()
    spy.emit.assert_called_once()
    emitted_event, emitted_payload = spy.emit.call_args[0]
    assert emitted_event == "settings_reloaded"
    assert isinstance(emitted_payload, Settings)
    # NOTE: gates the dead-bus regression (emit now reaches the production bus).
    # The live provider reload is driven by a real Settings object payload,
    # enabling type-guarded subscribers to apply it immediately.


async def test_provider_remove_emits_on_production_bus(tmp_yaml: Path) -> None:
    # Add first (without a bus, just to set up state)
    deps_no_bus = CommandDeps(event_bus=None)
    register_all_commands(deps_no_bus, registry=CommandRegistry.instance())
    await CommandRegistry.instance().dispatch(
        "provider", "add acme openai gpt-x fast", make_state()
    )

    # Now remove with a spy bus wired
    CommandRegistry.reset()
    spy = MagicMock()
    deps = CommandDeps(event_bus=spy)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = (
        await CommandRegistry.instance().dispatch("provider", "remove acme", make_state())
    ).text

    assert "✓" in result or "removed" in result.lower()
    spy.emit.assert_called_once()
    assert spy.emit.call_args[0][0] == "settings_reloaded"


async def test_provider_no_bus_does_not_crash(tmp_yaml: Path) -> None:
    deps = CommandDeps(event_bus=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (
        await CommandRegistry.instance().dispatch(
            "provider", "add acme openai gpt-x fast", make_state()
        )
    ).text
    assert "✓" in result or "added" in result.lower()


# ---------------------------------------------------------------------------
# Honesty — /provider messages must say "applied immediately"
# ---------------------------------------------------------------------------


async def test_provider_add_message_is_honest_about_timing(tmp_yaml: Path) -> None:
    """/provider add must say 'applied immediately' since emit now carries a real Settings."""
    deps = CommandDeps(event_bus=MagicMock())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (
        await CommandRegistry.instance().dispatch(
            "provider", "add acme openai gpt-x fast", make_state()
        )
    ).text
    assert "immediately" in result.lower()


# ---------------------------------------------------------------------------
# Guided add-flow — full end-to-end journey (Task 15)
#
# Drives every step of the guided catalog add-flow through the REAL
# CommandRegistry.dispatch path — the same path a real slash command from a
# real channel goes through. Mocks ONLY the AI-provider HTTP boundary
# (stackowl.providers.model_discovery.list_models) plus the OS keyring (so
# the test never touches a real keychain); every other step — browse, pick,
# token validation/persist, model pick, tier pick, final YAML write, and the
# live settings_reloaded emit — runs the real ProviderCommand code.
# ---------------------------------------------------------------------------


class _FakeKeyring:
    """In-memory stand-in for the ``keyring`` module — the AI-provider HTTP
    boundary is the only thing this journey mocks; the OS keychain is a local
    system dependency the test must not touch, so it gets a deterministic
    fake instead (same pattern as tests/commands/test_provider_command.py)."""

    _store: dict[str, str] = {}

    @classmethod
    def set_password(cls, service: str, _user: str, secret: str) -> None:
        cls._store[service] = secret

    @classmethod
    def get_password(cls, service: str, _user: str) -> str | None:
        return cls._store.get(service)


async def test_provider_guided_add_flow_full_journey(
    tmp_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """add (browse) -> add-pick groq -> add-token groq <TOKEN> -> add-model
    -> add-tier, then confirms the new provider is live and routable."""
    import sys

    from stackowl.config.settings import Settings
    from stackowl.providers.registry import ProviderRegistry

    _FakeKeyring._store.clear()
    monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring)
    monkeypatch.setattr(
        "stackowl.providers.model_discovery.list_models",
        AsyncMock(return_value=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]),
    )

    deps = CommandDeps(event_bus=MagicMock())
    register_all_commands(deps, registry=CommandRegistry.instance())
    registry = CommandRegistry.instance()

    # Step 1 — bare "add" browses the bundled catalog; groq (needs_api_key)
    # must be offered as a pick button.
    browse = await registry.dispatch("provider", "add", make_state())
    pick_commands = [a.command for a in browse.actions]
    assert "/provider add-pick groq" in pick_commands

    # Step 2 — picking groq (needs a token) prompts for add-token, it does
    # NOT jump straight to discovery (that's the keyless/local path).
    pick = await registry.dispatch("provider", "add-pick groq", make_state())
    assert "add-token groq" in pick.text

    # Step 3 — supplying the token drives the (mocked) live-discovery call,
    # which both validates the token and lists real models; the reply offers
    # one add-model button per discovered model. The raw token must never
    # leak into the response.
    raw_token = "sk-journey-test-RAW-TOKEN-001"  # noqa: S105 — test fixture value
    token_reply = await registry.dispatch(
        "provider", f"add-token groq {raw_token}", make_state()
    )
    assert raw_token not in token_reply.text
    model_actions = [
        a for a in token_reply.actions if a.command.startswith("/provider add-model groq")
    ]
    assert model_actions, f"expected add-model buttons, got: {token_reply.actions}"

    # Pull the picked model + api_key ref straight out of the real button
    # command instead of re-deriving it — proves the wiring end to end.
    _, _, catalog_name, model, api_key_ref = model_actions[0].command.split()
    assert catalog_name == "groq"
    assert api_key_ref.startswith("keychain:")
    assert raw_token not in api_key_ref
    assert _FakeKeyring._store[api_key_ref.removeprefix("keychain:")] == raw_token

    # Step 4 — model picked: offers one add-tier button per valid tier.
    model_reply = await registry.dispatch(
        "provider", f"add-model {catalog_name} {model} {api_key_ref}", make_state()
    )
    tier_actions = [
        a
        for a in model_reply.actions
        if a.command.startswith(f"/provider add-tier {catalog_name} {model} {api_key_ref}")
    ]
    assert tier_actions, f"expected add-tier buttons, got: {model_reply.actions}"
    _, _, _, _, _, tier = tier_actions[0].command.split()

    # Step 5 — tier picked: the LAST step of the guided flow, persists the
    # provider entry and applies it immediately.
    tier_reply = await registry.dispatch(
        "provider", f"add-tier {catalog_name} {model} {api_key_ref} {tier}", make_state()
    )
    assert "✓" in tier_reply.text
    assert "applied immediately" in tier_reply.text.lower()

    # Assert #1 — the new provider is correctly persisted in stackowl.yaml.
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    persisted = next(p for p in data["providers"] if p["name"] == catalog_name)
    assert persisted["protocol"] == "openai"
    assert persisted["default_model"] == model
    assert persisted["tiers"] == [tier]
    assert persisted["api_key"] == api_key_ref
    assert persisted["enabled"] is True
    assert persisted["base_url"] == "https://api.groq.com/openai/v1"

    # Assert #2 — a fresh ProviderRegistry built from that YAML (via a fresh
    # Settings load) can actually see/route to it — not just a YAML write.
    live_settings = Settings()
    live_registry = ProviderRegistry.from_settings(live_settings)
    live_provider = live_registry.get(catalog_name)
    assert live_provider.name == catalog_name
    assert live_registry.get_by_tier(tier).name == catalog_name
