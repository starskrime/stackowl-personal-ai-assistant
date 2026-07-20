"""Tests for the on-disk stackowl.yaml migration: legacy tier: -> tiers:."""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from stackowl.config.provider_tier_migration import migrate_legacy_tier_field


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_migrates_a_legacy_scalar_tier_to_a_one_item_tiers_list(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _write(cfg, "providers:\n  - name: groq\n    protocol: openai\n    tier: fast\n")

    changed = migrate_legacy_tier_field(cfg)

    assert changed is True
    with cfg.open("r", encoding="utf-8") as fh:
        data = _yaml().load(fh)
    assert data["providers"][0]["tiers"] == ["fast"]
    assert "tier" not in data["providers"][0]


def test_idempotent_on_an_already_migrated_file(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _write(cfg, "providers:\n  - name: groq\n    protocol: openai\n    tiers: [fast, standard]\n")

    changed = migrate_legacy_tier_field(cfg)

    assert changed is False
    with cfg.open("r", encoding="utf-8") as fh:
        data = _yaml().load(fh)
    assert data["providers"][0]["tiers"] == ["fast", "standard"]


def test_preserves_comments(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _write(
        cfg,
        "# my provider config\n"
        "providers:\n"
        "  - name: groq  # my key\n"
        "    protocol: openai\n"
        "    tier: fast\n",
    )

    migrate_legacy_tier_field(cfg)

    text = cfg.read_text(encoding="utf-8")
    assert "# my provider config" in text
    assert "# my key" in text


def test_migrates_only_entries_still_on_legacy_shape_in_a_mixed_file(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _write(
        cfg,
        "providers:\n"
        "  - name: legacy-one\n"
        "    protocol: openai\n"
        "    tier: fast\n"
        "  - name: already-migrated\n"
        "    protocol: openai\n"
        "    tiers: [powerful]\n",
    )

    changed = migrate_legacy_tier_field(cfg)

    assert changed is True
    with cfg.open("r", encoding="utf-8") as fh:
        data = _yaml().load(fh)
    by_name = {e["name"]: e for e in data["providers"]}
    assert by_name["legacy-one"]["tiers"] == ["fast"]
    assert by_name["already-migrated"]["tiers"] == ["powerful"]


def test_missing_file_is_a_no_op(tmp_path: Path) -> None:
    cfg = tmp_path / "does-not-exist.yaml"
    assert migrate_legacy_tier_field(cfg) is False


def test_no_providers_key_is_a_no_op(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _write(cfg, "test_mode: true\n")
    assert migrate_legacy_tier_field(cfg) is False


def test_malformed_yaml_is_left_untouched_not_raised(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _write(cfg, "providers: [this is not: valid: yaml: at all\n")
    original = cfg.read_text(encoding="utf-8")

    changed = migrate_legacy_tier_field(cfg)

    assert changed is False
    assert cfg.read_text(encoding="utf-8") == original
