"""Dispatch test — /webhook is wired through CommandRegistry."""
from __future__ import annotations
import pytest
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from stackowl.commands.webhook_command import WebhookCommand
from tests._story_6_7_helpers import db, make_settings, make_state, no_test_mode_guard  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_webhook_usage_with_no_args() -> None:
    deps = CommandDeps(db=object(), settings=make_settings())  # type: ignore[arg-type]
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("webhook", "", make_state())
    assert "Usage:" in result


async def test_webhook_register_writes_yaml_via_registry(tmp_path, monkeypatch) -> None:
    """/webhook register now really writes stackowl.yaml (via the full registry
    dispatch path), not just prints instructions."""
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))
    CommandRegistry.reset()
    deps = CommandDeps(db=object(), settings=make_settings())  # type: ignore[arg-type]
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "webhook", "register mygithub timestamp_header=X-Hub-Signature", make_state()
    )
    assert "✓" in result
    from stackowl.commands.config_helpers import load_yaml
    data = load_yaml(config_file)
    assert data["webhook"]["sources"]["mygithub"]["timestamp_header"] == "X-Hub-Signature"


async def test_webhook_register_writes_real_config(tmp_path, monkeypatch, db) -> None:
    """/webhook register must write stackowl.yaml, not just print instructions."""
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))
    settings = make_settings()
    cmd = WebhookCommand(db=db, settings=settings)

    result = await cmd.handle(
        "register acme timestamp_header=X-Ts-Header", make_state()
    )

    assert "✓" in result
    from stackowl.commands.config_helpers import load_yaml
    data = load_yaml(config_file)
    assert data["webhook"]["enabled"] is True
    assert data["webhook"]["sources"]["acme"]["timestamp_header"] == "X-Ts-Header"
    assert data["webhook"]["sources"]["acme"]["secret"].startswith(("keychain:", "file:"))


async def test_webhook_register_requires_anti_replay_mechanism(db) -> None:
    """Neither timestamp_header nor delivery_id_header given → clear rejection,
    never a silent guess at a vendor-specific header name."""
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle("register acme", make_state())

    assert "✗" in result
    assert "timestamp_header" in result
    assert "delivery_id_header" in result


async def test_webhook_register_auto_generates_secret_and_shows_it_once(tmp_path, monkeypatch, db) -> None:
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle(
        "register acme delivery_id_header=X-Delivery-Id", make_state()
    )

    assert "won't be shown again" in result.lower() or "shown once" in result.lower()
    from stackowl.commands.config_helpers import load_yaml
    data = load_yaml(config_file)
    ref = data["webhook"]["sources"]["acme"]["secret"]
    assert ref.startswith(("keychain:", "file:"))


async def test_webhook_register_with_supplied_secret_never_echoes_it(tmp_path, monkeypatch, db) -> None:
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle(
        "register acme delivery_id_header=X-Delivery-Id secret=my-raw-secret-value",
        make_state(),
    )

    assert "my-raw-secret-value" not in result


async def test_webhook_not_configured_when_db_none() -> None:
    CommandRegistry.reset()
    deps = CommandDeps(db=None, settings=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("webhook", "", make_state())
    assert "not configured" in result


async def test_webhook_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("webhook", "", make_state())


async def test_webhook_disable_writes_real_config(tmp_path, monkeypatch, db) -> None:
    """/webhook disable must flip sources[source].enabled to False in stackowl.yaml."""
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))
    from stackowl.commands.config_helpers import save_yaml
    save_yaml(config_file, {
        "webhook": {
            "enabled": True,
            "sources": {
                "acme": {
                    "enabled": True,
                    "secret": "keychain:x",
                    "delivery_id_header": "X-Id",
                }
            },
        }
    })
    settings = make_settings()
    cmd = WebhookCommand(db=db, settings=settings)

    result = await cmd.handle("disable acme", make_state())

    assert "✓" in result
    from stackowl.commands.config_helpers import load_yaml
    data = load_yaml(config_file)
    assert data["webhook"]["sources"]["acme"]["enabled"] is False


async def test_webhook_list_reads_live_yaml_not_frozen_settings(tmp_path, monkeypatch, db) -> None:
    """/webhook list must reflect the live YAML file, not the settings snapshot
    frozen at WebhookCommand construction time (the command is a singleton;
    ``self._settings`` is never refreshed after a live register/disable)."""
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))
    # settings is constructed BEFORE "acme" ever exists on disk — this is the
    # frozen snapshot the (singleton, in real use) command was built with.
    settings = make_settings()
    cmd = WebhookCommand(db=db, settings=settings)

    from stackowl.commands.config_helpers import save_yaml
    save_yaml(config_file, {
        "webhook": {
            "enabled": True,
            "sources": {
                "acme": {
                    "enabled": True,
                    "secret": "keychain:x",
                    "delivery_id_header": "X-Id",
                }
            },
        }
    })

    result = await cmd.handle("list", make_state())

    assert "acme" in result
    assert "[enabled]" in result


async def test_webhook_handle_catches_register_exception(tmp_path, monkeypatch, db) -> None:
    """A raising store_secret (e.g. keyring outage) must surface as a clean
    ✗ /webhook error, never an unhandled traceback escaping handle()."""
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))

    def _boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("keyring unavailable")

    monkeypatch.setattr("stackowl.commands.webhook_command.store_secret", _boom)
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle("register acme timestamp_header=X-Ts", make_state())

    assert result.startswith("✗ /webhook register:")
    assert "keyring unavailable" in result
