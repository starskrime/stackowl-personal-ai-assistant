"""Tests for ProviderCatalog — loading, validation, overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stackowl.setup.provider_catalog import PROTOCOLS, ProviderCatalog, ProviderEntry


def test_catalog_loads_all_bundled_yaml_files() -> None:
    entries = ProviderCatalog.load()
    assert len(entries) == 15, f"Expected 15 bundled providers, got {len(entries)}: {[e.name for e in entries]}"


def test_catalog_protocols_are_one_of_four() -> None:
    entries = ProviderCatalog.load()
    for entry in entries:
        assert entry.protocol in PROTOCOLS, f"Provider '{entry.name}' has unknown protocol '{entry.protocol}'"


def test_catalog_sort_order_locals_last_then_custom_last() -> None:
    entries = ProviderCatalog.load()
    names = [e.name for e in entries]
    assert names[-1] == "custom", f"'custom' should be last; got: {names}"
    # All locals should come after all non-local non-custom entries
    non_local_non_custom = [e for e in entries if not e.is_local and e.name != "custom"]
    locals_ = [e for e in entries if e.is_local]
    if non_local_non_custom and locals_:
        last_regular_idx = max(i for i, e in enumerate(entries) if not e.is_local and e.name != "custom")
        first_local_idx = min(i for i, e in enumerate(entries) if e.is_local)
        assert last_regular_idx < first_local_idx, "Locals should come after all regular providers"


def test_user_override_replaces_bundled_entry_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    monkeypatch.delenv("STACKOWL_CONFIG_FILE", raising=False)

    from stackowl.paths import StackowlHome
    StackowlHome.ensure_exists()

    override = {
        "name": "openai",
        "label": "OpenAI (custom override)",
        "protocol": "openai",
        "base_url": "https://custom.openai.example.com/v1",
        "default_model": "gpt-4o-mini",
        "tier": "fast",
        "needs_api_key": True,
    }
    (StackowlHome.providers_dir() / "openai.yaml").write_text(
        yaml.dump(override), encoding="utf-8"
    )

    entries = ProviderCatalog.load()
    openai_entry = next(e for e in entries if e.name == "openai")
    assert openai_entry.label == "OpenAI (custom override)"
    assert openai_entry.base_url == "https://custom.openai.example.com/v1"
    assert openai_entry.tier == "fast"
    # Count must remain 15 (override, not addition)
    assert len(entries) == 15


def test_user_can_add_new_provider_beyond_bundled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    monkeypatch.delenv("STACKOWL_CONFIG_FILE", raising=False)

    from stackowl.paths import StackowlHome
    StackowlHome.ensure_exists()

    new_provider = {
        "name": "mycompany",
        "label": "My Company AI",
        "protocol": "openai",
        "base_url": "https://ai.mycompany.com/v1",
        "default_model": "mymodel-v1",
        "tier": "powerful",
        "needs_api_key": True,
    }
    (StackowlHome.providers_dir() / "mycompany.yaml").write_text(
        yaml.dump(new_provider), encoding="utf-8"
    )

    entries = ProviderCatalog.load()
    assert len(entries) == 16
    names = [e.name for e in entries]
    assert "mycompany" in names


def test_provider_entry_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="unknown protocol"):
        ProviderEntry(
            name="bad",
            label="Bad Provider",
            protocol="not-a-real-protocol",
            base_url="https://example.com/v1",
            default_model="model-x",
        )
