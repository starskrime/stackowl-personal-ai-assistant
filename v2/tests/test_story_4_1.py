"""Story 4.1 — OwlDNA, OwlSource, extended manifest/registry, settings owls list."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ManifestValidationError
from stackowl.owls.base import OwlSource
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry, _make_default_secretary
from stackowl.owls.yaml_source import YamlOwlSource


# ---------------------------------------------------------------------------
# OwlDNA
# ---------------------------------------------------------------------------


class TestOwlDNA:
    def test_defaults_are_in_range(self) -> None:
        dna = OwlDNA()
        for field in (
            "challenge_level",
            "verbosity",
            "curiosity",
            "formality",
            "creativity",
            "precision",
        ):
            value = getattr(dna, field)
            assert 0.0 <= value <= 1.0
            assert value == pytest.approx(0.5)
        assert dna.decay_rate_per_week == pytest.approx(0.05)

    def test_dna_is_frozen(self) -> None:
        dna = OwlDNA()
        with pytest.raises(ValidationError):
            dna.challenge_level = 0.9  # type: ignore[misc]

    def test_dna_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            OwlDNA(unknown_trait=0.5)  # type: ignore[call-arg]

    def test_dna_rejects_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            OwlDNA(challenge_level=1.5)
        with pytest.raises(ValidationError):
            OwlDNA(verbosity=-0.1)

    def test_mutate_returns_new_instance(self) -> None:
        dna = OwlDNA()
        new_dna = dna.mutate("curiosity", 0.2)
        assert new_dna is not dna
        assert new_dna.curiosity == pytest.approx(0.7)
        assert dna.curiosity == pytest.approx(0.5)

    def test_mutate_clamps_upper_bound(self) -> None:
        dna = OwlDNA(formality=0.9)
        new_dna = dna.mutate("formality", 0.5)
        assert new_dna.formality == pytest.approx(1.0)

    def test_mutate_clamps_lower_bound(self) -> None:
        dna = OwlDNA(precision=0.1)
        new_dna = dna.mutate("precision", -0.5)
        assert new_dna.precision == pytest.approx(0.0)

    def test_mutate_unknown_trait_raises(self) -> None:
        dna = OwlDNA()
        with pytest.raises(ManifestValidationError) as exc:
            dna.mutate("nonexistent", 0.1)
        assert exc.value.field == "dna_trait"

    def test_mutate_rejects_non_mutable_trait(self) -> None:
        """decay_rate_per_week is not in the mutable trait set."""
        dna = OwlDNA()
        with pytest.raises(ManifestValidationError):
            dna.mutate("decay_rate_per_week", 0.1)

    def test_dominant_traits_sorted_by_deviation(self) -> None:
        dna = OwlDNA(
            challenge_level=0.5,
            verbosity=0.9,
            curiosity=0.1,
            formality=0.5,
            creativity=0.75,
            precision=0.5,
        )
        top = dna.dominant_traits(3)
        assert len(top) == 3
        names = [name for name, _ in top]
        # Most-deviating first: |0.9-0.5|=0.4, |0.1-0.5|=0.4, |0.75-0.5|=0.25
        assert "verbosity" in names[:2]
        assert "curiosity" in names[:2]
        assert names[2] == "creativity"

    def test_dominant_traits_default_n_three(self) -> None:
        dna = OwlDNA()
        assert len(dna.dominant_traits()) == 3

    def test_dominant_traits_negative_n_returns_empty(self) -> None:
        dna = OwlDNA()
        assert dna.dominant_traits(-5) == []


# ---------------------------------------------------------------------------
# OwlSource / YamlOwlSource
# ---------------------------------------------------------------------------


class _StubSource(OwlSource):
    @property
    def source_name(self) -> str:
        return "stub"

    def list_owls(self) -> list[OwlAgentManifest]:
        return []


class TestOwlSource:
    def test_owl_source_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            OwlSource()  # type: ignore[abstract]

    def test_stub_source_implements_interface(self) -> None:
        source = _StubSource()
        assert source.source_name == "stub"
        assert source.list_owls() == []

    def test_yaml_source_has_expected_name(self) -> None:
        source = YamlOwlSource([])
        assert source.source_name == "yaml"

    def test_yaml_source_returns_owls(self) -> None:
        manifest = OwlAgentManifest(
            name="amelia",
            role="developer",
            system_prompt="Helpful dev.",
            model_tier="standard",
        )
        source = YamlOwlSource([manifest])
        assert source.list_owls() == [manifest]

    def test_yaml_source_returns_defensive_copy(self) -> None:
        manifest = OwlAgentManifest(
            name="nora",
            role="analyst",
            system_prompt="Be analytical.",
            model_tier="fast",
        )
        source = YamlOwlSource([manifest])
        listed = source.list_owls()
        listed.append(manifest)  # mutating returned list must not affect source
        assert len(source.list_owls()) == 1


# ---------------------------------------------------------------------------
# OwlAgentManifest extended fields
# ---------------------------------------------------------------------------


class TestOwlAgentManifest:
    def _base(self, **overrides: object) -> OwlAgentManifest:
        defaults: dict[str, object] = {
            "name": "miko",
            "role": "researcher",
            "system_prompt": "Research and report.",
            "model_tier": "powerful",
        }
        defaults.update(overrides)
        return OwlAgentManifest(**defaults)  # type: ignore[arg-type]

    def test_new_field_defaults(self) -> None:
        manifest = self._base()
        assert manifest.timeout_seconds == pytest.approx(30.0)
        assert manifest.max_concurrent_requests == 1
        assert isinstance(manifest.dna, OwlDNA)
        assert manifest.dna.challenge_level == pytest.approx(0.5)

    def test_manifest_is_frozen(self) -> None:
        manifest = self._base()
        with pytest.raises(ValidationError):
            manifest.role = "anything"  # type: ignore[misc]

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            self._base(timeout_seconds=0.0)
        with pytest.raises(ValidationError):
            self._base(timeout_seconds=-1.0)

    def test_max_concurrent_at_least_one(self) -> None:
        with pytest.raises(ValidationError):
            self._base(max_concurrent_requests=0)

    def test_dna_can_be_supplied(self) -> None:
        custom = OwlDNA(curiosity=0.8)
        manifest = self._base(dna=custom)
        assert manifest.dna.curiosity == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# OwlRegistry
# ---------------------------------------------------------------------------


class TestOwlRegistry:
    def _manifest(self, name: str) -> OwlAgentManifest:
        return OwlAgentManifest(
            name=name,
            role="generic",
            system_prompt="Be helpful.",
            model_tier="fast",
        )

    def test_register_source_appends(self) -> None:
        registry = OwlRegistry()
        source = _StubSource()
        registry.register_source(source)
        assert source in registry.sources()
        assert len(registry.sources()) == 1

    def test_list_returns_sorted(self) -> None:
        registry = OwlRegistry()
        registry.register(self._manifest("zeta"))
        registry.register(self._manifest("alpha"))
        registry.register(self._manifest("mira"))
        names = [m.name for m in registry.list()]
        assert names == ["alpha", "mira", "zeta"]

    def test_duplicate_register_raises(self) -> None:
        registry = OwlRegistry()
        registry.register(self._manifest("duplicate"))
        with pytest.raises(ManifestValidationError):
            registry.register(self._manifest("duplicate"))

    def test_make_default_secretary_returns_manifest(self) -> None:
        secretary = _make_default_secretary()
        assert secretary.name == "secretary"
        assert secretary.role == "primary-assistant"
        assert secretary.model_tier == "powerful"


def _settings_with_owls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    include_secretary: bool,
) -> Settings:
    cfg = tmp_path / "stackowl.yaml"
    owls_block: list[dict[str, object]] = [
        {
            "name": "nora",
            "role": "analyst",
            "system_prompt": "Analyze inputs.",
            "model_tier": "fast",
        }
    ]
    if include_secretary:
        owls_block.append({
            "name": "secretary",
            "role": "primary-assistant",
            "system_prompt": "Custom secretary prompt.",
            "model_tier": "powerful",
        })
    cfg.write_text(
        yaml.dump({
            "test_mode": True,
            "owls": owls_block,
            "providers": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    TestModeGuard._active = False  # type: ignore[attr-defined]
    return Settings()


class TestRegistryFromSettings:
    def test_injects_secretary_when_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        settings = _settings_with_owls(monkeypatch, tmp_path, include_secretary=False)
        registry = OwlRegistry.from_settings(settings)
        assert registry.has_secretary()
        names = {m.name for m in registry.all()}
        assert names == {"secretary", "nora"}

    def test_uses_supplied_secretary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        settings = _settings_with_owls(monkeypatch, tmp_path, include_secretary=True)
        registry = OwlRegistry.from_settings(settings)
        secretary = registry.get("secretary")
        assert secretary.system_prompt == "Custom secretary prompt."
        assert registry.has_secretary()

    def test_duplicate_name_in_settings_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "stackowl.yaml"
        cfg.write_text(
            yaml.dump({
                "test_mode": True,
                "owls": [
                    {
                        "name": "dupe",
                        "role": "x",
                        "system_prompt": "y",
                        "model_tier": "fast",
                    },
                    {
                        "name": "dupe",
                        "role": "x2",
                        "system_prompt": "y2",
                        "model_tier": "fast",
                    },
                ],
                "providers": [],
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
        TestModeGuard._active = False  # type: ignore[attr-defined]
        settings = Settings()
        with pytest.raises(ManifestValidationError):
            OwlRegistry.from_settings(settings)


# ---------------------------------------------------------------------------
# Settings.owls + autonomy_level
# ---------------------------------------------------------------------------


class TestSettingsOwlsField:
    def test_autonomy_level_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "stackowl.yaml"
        cfg.write_text(yaml.dump({"test_mode": True, "providers": []}), encoding="utf-8")
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
        TestModeGuard._active = False  # type: ignore[attr-defined]
        settings = Settings()
        assert settings.autonomy_level == "medium"
        assert settings.owls == []

    def test_autonomy_level_loaded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "stackowl.yaml"
        cfg.write_text(
            yaml.dump({"test_mode": True, "providers": [], "autonomy_level": "high"}),
            encoding="utf-8",
        )
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
        TestModeGuard._active = False  # type: ignore[attr-defined]
        settings = Settings()
        assert settings.autonomy_level == "high"

    def test_autonomy_level_rejects_invalid(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "stackowl.yaml"
        cfg.write_text(
            yaml.dump({"test_mode": True, "providers": [], "autonomy_level": "extreme"}),
            encoding="utf-8",
        )
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
        TestModeGuard._active = False  # type: ignore[attr-defined]
        with pytest.raises(ValidationError):
            Settings()

    def test_owls_loads_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        settings = _settings_with_owls(monkeypatch, tmp_path, include_secretary=False)
        assert len(settings.owls) == 1
        assert settings.owls[0].name == "nora"
        # All extended-manifest defaults should be set:
        assert settings.owls[0].timeout_seconds == pytest.approx(30.0)
        assert settings.owls[0].max_concurrent_requests == 1
        assert isinstance(settings.owls[0].dna, OwlDNA)
