"""Tests for ProviderCatalog — loading, validation, overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stackowl.setup.provider_catalog import (
    _BUNDLED_DIR,
    PROTOCOLS,
    ProviderCatalog,
    ProviderEntry,
)


def _bundled_names() -> set[str]:
    """The names the catalog SHOULD contain, derived from the directory it loads.

    D18.6: this used to be the literal `49`, and before that `17`, and before that
    `15`. Every provider added forced an edit here — one commit added 32 at once —
    and the "fix" was always to bump a number, which is a test that measures the
    tree's growth rather than any property of it.

    Deriving it is also STRICTLY STRONGER than the count was. The old assertion
    caught a YAML that failed to load; comparing the NAME SET additionally catches
    a file whose declared `name` disagrees with its filename, and two files
    collapsing onto one name — neither of which a count can see.
    """
    return {path.stem for path in _BUNDLED_DIR.glob("*.yaml")}


def test_every_bundled_yaml_becomes_exactly_one_entry() -> None:
    entries = ProviderCatalog.load()
    loaded = [e.name for e in entries]

    assert len(loaded) == len(set(loaded)), (
        f"two bundled files collapsed onto one name: "
        f"{sorted(n for n in loaded if loaded.count(n) > 1)}"
    )
    assert set(loaded) == _bundled_names(), (
        f"the catalog does not match the directory it loads.\n"
        f"  on disk, not loaded: {sorted(_bundled_names() - set(loaded))}\n"
        f"  loaded, not on disk: {sorted(set(loaded) - _bundled_names())}"
    )


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


def test_a_user_file_CANNOT_replace_a_bundled_entry_by_name(
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
    # INVERTED 2026-08-21 (ESC-23). This asserted that the user file WON. Bakir chose
    # additive-only: a user provider may add a name the bundle does not carry, never
    # redefine a built-in one. `ProviderEntry` carries `base_url`, and the add-token
    # flow sends that URL the operator's raw credential to validate it — so a
    # replacement let a local file redirect the next token typed for "openai". The
    # test is inverted rather than deleted so the change of behaviour stays legible.
    assert openai_entry.label != "OpenAI (custom override)"
    assert openai_entry.base_url != "https://custom.openai.example.com/v1"
    # The colliding file contributed nothing and added nothing — stated as the
    # relationship it is, not as the literal 49 it happened to equal (D18.6).
    assert {e.name for e in entries} == _bundled_names()


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
    names = {e.name for e in entries}

    # The property is "the user's file ADDS one, and displaces nothing" — which is
    # what `== 50` was expressing as a literal (49 bundled + 1). Written as the
    # relationship, adding a bundled provider never touches this test (D18.6).
    assert names == _bundled_names() | {"mycompany"}


def test_provider_entry_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="unknown protocol"):
        ProviderEntry(
            name="bad",
            label="Bad Provider",
            protocol="not-a-real-protocol",
            base_url="https://example.com/v1",
            default_model="model-x",
        )


def test_provider_entry_category_defaults_empty() -> None:
    entry = ProviderEntry(
        name="x", label="X", protocol="openai",
        base_url="https://x.example.com/v1", default_model="m",
    )
    assert entry.category == ()


def test_search_matches_name_label_or_category(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.setup import provider_catalog as mod

    fake = [
        ProviderEntry(
            name="groq", label="Groq", protocol="openai",
            base_url="https://api.groq.com/openai/v1", default_model="llama-3.3-70b-versatile",
            category=("free-tier", "fast-inference"),
        ),
        ProviderEntry(
            name="openai", label="OpenAI", protocol="openai",
            base_url="https://api.openai.com/v1", default_model="gpt-4o",
        ),
    ]
    monkeypatch.setattr(mod.ProviderCatalog, "load", classmethod(lambda cls: fake))

    assert [e.name for e in mod.ProviderCatalog.search("groq")] == ["groq"]
    assert [e.name for e in mod.ProviderCatalog.search("free")] == ["groq"]
    assert [e.name for e in mod.ProviderCatalog.search("GROQ")] == ["groq"]
    assert mod.ProviderCatalog.search("nonexistent-xyz") == []


def test_browse_filters_by_category(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.setup import provider_catalog as mod

    fake = [
        ProviderEntry(
            name="groq", label="Groq", protocol="openai",
            base_url="https://api.groq.com/openai/v1", default_model="llama-3.3-70b-versatile",
            category=("free-tier",),
        ),
        ProviderEntry(
            name="openai", label="OpenAI", protocol="openai",
            base_url="https://api.openai.com/v1", default_model="gpt-4o",
        ),
    ]
    monkeypatch.setattr(mod.ProviderCatalog, "load", classmethod(lambda cls: fake))

    assert [e.name for e in mod.ProviderCatalog.browse("free-tier")] == ["groq"]
    assert len(mod.ProviderCatalog.browse(None)) == 2
