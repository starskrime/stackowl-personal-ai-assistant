# Multi-Tier Provider Membership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one configured provider (single connection/API key) belong to multiple routing tiers at once, replacing today's one-tier-per-provider constraint.

**Architecture:** `ProviderConfig.tier: Literal[...]` becomes `ProviderConfig.tiers: tuple[Literal[...], ...]`, with a `model_validator(mode="before")` that transparently accepts a legacy `tier=<str>` constructor kwarg (normalized to a one-item tuple) — this keeps the ~50 existing test/production call sites that construct `ProviderConfig(tier=..., ...)` working with zero changes, since none of them read the `.tier` attribute back. `ProviderRegistry._tiers` becomes `dict[str, tuple[str, ...]]`; every membership check across the registry, `TierSelector`, and both slash commands changes from equality (`t == tier`) to containment (`tier in t`). A real, idempotent, comment-preserving rewrite of `stackowl.yaml` (legacy `tier:` scalar → `tiers:` list) is wired into `Settings`'s YAML-loading source — the single choke point every `Settings()` construction across the whole codebase already goes through — so no per-call-site migration hook is needed and no boot path can bypass it.

**Tech Stack:** Python 3.13, pydantic, pydantic-settings, ruamel.yaml, pytest/pytest-asyncio.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-20-multi-tier-provider-membership-design.md` — every requirement here traces to a section there.
- `ProviderConfig.tiers` requires at least one entry, no duplicates.
- Only the live `stackowl.yaml` schema changes — `setup/provider_catalog.py`'s `ProviderEntry.tier` (bundled catalog, add-time suggestion only) stays singular, untouched.
- `/provider set-tier` and `/tier add` **add** a tier to the list (never replace). `/tier remove` removes just one tier, auto-disabling (never emptying the list) only if it was the provider's last tier.
- The on-disk migration is a real file rewrite (comment-preserving via ruamel.yaml), not merely an in-memory shim — but pydantic-level validation must ALSO independently accept the legacy shape (defense in depth), since `Settings()` is constructed in many places across the codebase and the file-rewrite must never be a boot-blocking prerequisite.
- 4-point logging (entry/decision/step/exit) on every new/modified method that does real work, matching this codebase's existing convention.
- `ruff check src/` and `mypy src/` must introduce zero NEW errors in any file this plan touches (the repo has pre-existing, unrelated lint/type findings elsewhere — not your concern; verify by confirming a touched file contributes none of the errors a full run reports).
- Never run the full `pytest` suite (hangs on this box, established at project level) — scope every test run to the files this plan touches.

---

## Phase 1 — Schema & registry core

### Task 1: `ProviderConfig.tiers` + legacy `tier=` constructor alias

**Files:**
- Modify: `src/stackowl/config/provider.py`
- Test: `tests/config/test_provider_config.py`

**Interfaces:**
- Produces: `ProviderConfig.tiers: tuple[Literal["fast", "standard", "powerful", "local"], ...]` (required, min length 1, no duplicates); a `model_validator(mode="before")` that accepts either `tiers=<sequence>` or the legacy `tier=<str>` kwarg.

- [ ] **Step 1: Write the failing tests**

Add to `tests/config/test_provider_config.py` (the file currently has two `cooldown_hours` tests using `ProviderConfig(tier="fast", ...)` — leave those as-is, they'll keep passing via the new validator; add these new tests alongside them):

```python
import pytest
from pydantic import ValidationError


def test_tiers_accepts_a_tuple_directly() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tiers=("fast", "standard"),
    )
    assert cfg.tiers == ("fast", "standard")


def test_legacy_tier_kwarg_is_normalized_to_a_one_item_tiers_tuple() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tier="fast",
    )
    assert cfg.tiers == ("fast",)
    assert not hasattr(cfg, "tier")  # the field itself no longer exists


def test_tiers_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m", tiers=())


def test_tiers_rejects_duplicates() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m", tiers=("fast", "fast"))


def test_tiers_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m", tiers=("ultra",))


def test_neither_tier_nor_tiers_given_is_required_error() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/config/test_provider_config.py -v`
Expected: FAIL — `tiers` doesn't exist yet (`TypeError`/`ValidationError` on unexpected/missing field, depending on the assertion).

- [ ] **Step 3: Implement**

In `src/stackowl/config/provider.py`, add the import and replace the `tier` field:

```python
from typing import Literal

from pydantic import BaseModel, model_validator
```

Replace line 24 (`tier: Literal["fast", "standard", "powerful", "local"]`) with:

```python
    # F-multi-tier — a provider can belong to more than one routing tier at
    # once (e.g. the same key serving both "fast" and "standard"). At least
    # one entry, no duplicates (enforced below). The legacy singular `tier`
    # constructor kwarg is still accepted (see the validator below) so the
    # ~50 existing call sites across this codebase that build
    # ProviderConfig(tier="fast", ...) keep working unchanged — none of them
    # read the removed `.tier` attribute back, only `.tiers`.
    tiers: tuple[Literal["fast", "standard", "powerful", "local"], ...]
```

Add the validator right after the class docstring's field block (after `cooldown_hours`, at the end of the field list, before any existing methods — this class currently has no methods, so add it as the first method):

```python
    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_tier(cls, data: object) -> object:
        """Accept a legacy singular ``tier=<str>`` constructor kwarg (or dict
        key) as an alias for ``tiers=(<str>,)``. Runs BEFORE field validation,
        so a caller that still passes ``tier="fast"`` — whether constructing
        ProviderConfig directly in Python or via a raw YAML/dict that hasn't
        been through the on-disk migration yet — is normalized here rather
        than rejected. ``tiers`` wins if both are somehow present."""
        if not isinstance(data, dict) or "tiers" in data or "tier" not in data:
            return data
        legacy = data.pop("tier")
        data["tiers"] = (legacy,) if isinstance(legacy, str) else tuple(legacy)
        return data

    @field_validator("tiers")
    @classmethod
    def _validate_tiers(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("tiers must contain at least one entry")
        if len(set(value)) != len(value):
            raise ValueError(f"tiers must not contain duplicates: {value}")
        return value
```

Add `field_validator` to the existing `from pydantic import BaseModel, model_validator` import line, making it:

```python
from pydantic import BaseModel, field_validator, model_validator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/config/test_provider_config.py -v`
Expected: PASS (all, including the 2 pre-existing `cooldown_hours` tests, unmodified).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/config/provider.py && uv run mypy src/stackowl/config/provider.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/config/provider.py tests/config/test_provider_config.py
git commit -m "feat(config): ProviderConfig.tiers (multi-tier membership) with legacy tier= alias"
```

---

### Task 2: On-disk migration module (legacy `tier:` scalar → `tiers:` list)

**Files:**
- Create: `src/stackowl/config/provider_tier_migration.py`
- Test: `tests/config/test_provider_tier_migration.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (this module operates on raw YAML, not `ProviderConfig`).
- Produces: `def migrate_legacy_tier_field(path: Path) -> bool` — returns `True` iff it rewrote the file, `False` if nothing needed migrating (missing file, no `providers:` key, or every entry already on `tiers:`). Never raises — a malformed file is left untouched and logged.

- [ ] **Step 1: Write the failing tests**

Create `tests/config/test_provider_tier_migration.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/config/test_provider_tier_migration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.config.provider_tier_migration'`.

- [ ] **Step 3: Implement**

Create `src/stackowl/config/provider_tier_migration.py`:

```python
"""On-disk stackowl.yaml migration: legacy provider `tier:` scalar -> `tiers:` list.

Wired into ``_YamlSource._load()`` (settings.py) — the single choke point
every ``Settings()`` construction across the codebase already goes through —
so no boot path or CLI entry point can bypass it. Idempotent: an
already-migrated entry (or the whole file) is a no-op. Uses ruamel.yaml
(comment-preserving), matching this codebase's existing config-write
convention (commands/config_helpers.py, setup/yaml_writer.py).

This is a hygiene/visibility feature, NOT a boot-blocking prerequisite —
ProviderConfig's own model_validator independently accepts the legacy shape
(defense in depth), so a boot never crashes even if this migration hasn't
run yet for some reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from stackowl.infra.observability import log


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y


def migrate_legacy_tier_field(path: Path) -> bool:
    """Rewrite any provider entry still on legacy `tier:` to `tiers:`.

    Returns True iff the file was rewritten. Never raises: a missing file, a
    file with no `providers:` list, or a parse failure are all treated as
    "nothing to migrate" and logged appropriately — a migration bug must
    never block a boot that would otherwise succeed.
    """
    log.config.debug(
        "[config] provider_tier_migration.migrate: entry", extra={"_fields": {"path": str(path)}}
    )
    if not path.exists():
        log.config.debug("[config] provider_tier_migration.migrate: exit — file missing")
        return False

    yml = _yaml()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data: Any = yml.load(fh)
    except Exception as exc:
        log.config.warning(
            "[config] provider_tier_migration.migrate: exit — parse failed, leaving untouched",
            extra={"_fields": {"path": str(path), "error": str(exc)}},
        )
        return False

    if not isinstance(data, dict):
        log.config.debug("[config] provider_tier_migration.migrate: exit — not a mapping")
        return False
    providers = data.get("providers")
    if not isinstance(providers, list):
        log.config.debug("[config] provider_tier_migration.migrate: exit — no providers list")
        return False

    migrated: list[str] = []
    for entry in providers:
        if not isinstance(entry, dict):
            continue
        if "tiers" in entry or "tier" not in entry:
            continue
        legacy = entry.pop("tier")
        entry["tiers"] = [legacy]
        migrated.append(str(entry.get("name", "?")))

    if not migrated:
        log.config.debug("[config] provider_tier_migration.migrate: exit — nothing to migrate")
        return False

    with path.open("w", encoding="utf-8") as fh:
        yml.dump(data, fh)
    log.config.info(
        "[config] provider_tier_migration.migrate: exit — rewrote legacy tier field",
        extra={"_fields": {"path": str(path), "providers_migrated": migrated}},
    )
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/config/test_provider_tier_migration.py -v`
Expected: PASS (all 7).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/config/provider_tier_migration.py && uv run mypy src/stackowl/config/provider_tier_migration.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/config/provider_tier_migration.py tests/config/test_provider_tier_migration.py
git commit -m "feat(config): add idempotent on-disk migration for legacy provider tier: -> tiers:"
```

---

### Task 3: Wire the migration into `Settings`'s YAML source + fix the provider-summary log line

**Files:**
- Modify: `src/stackowl/config/settings.py`
- Test: `tests/config/test_settings_provider_migration.py`

**Interfaces:**
- Consumes: `migrate_legacy_tier_field` (Task 2).

- [ ] **Step 1: Write the failing tests**

Create `tests/config/test_settings_provider_migration.py`:

```python
"""Confirms Settings() triggers the on-disk provider-tier migration exactly
once per load, at the single choke point every Settings() construction
(across the whole codebase) already goes through."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_settings_construction_migrates_a_legacy_yaml_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        "test_mode: true\n"
        "providers:\n"
        "  - name: groq\n"
        "    protocol: openai\n"
        "    default_model: m\n"
        "    tier: fast\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))

    from stackowl.config.settings import Settings

    settings = Settings()

    assert settings.providers[0].tiers == ("fast",)
    # The FILE itself was rewritten, not just normalized in memory.
    from ruamel.yaml import YAML
    with cfg.open("r", encoding="utf-8") as fh:
        raw = YAML().load(fh)
    assert raw["providers"][0]["tiers"] == ["fast"]
    assert "tier" not in raw["providers"][0]


def test_settings_construction_is_idempotent_on_an_already_migrated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "stackowl.yaml"
    original = (
        "test_mode: true\n"
        "providers:\n"
        "  - name: groq\n"
        "    protocol: openai\n"
        "    default_model: m\n"
        "    tiers: [fast, standard]\n"
    )
    cfg.write_text(original, encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))

    from stackowl.config.settings import Settings

    Settings()

    assert cfg.read_text(encoding="utf-8") == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/config/test_settings_provider_migration.py -v`
Expected: FAIL — `settings.providers[0].tiers == ("fast",)` would currently ALSO pass thanks to Task 1's lenient validator (the in-memory normalization already works), but the SECOND assertion (the file itself was rewritten) fails — `raw["providers"][0]` still has `tier`, no `tiers`. Confirm this is the actual failure (not a different error) before proceeding.

- [ ] **Step 3: Implement**

In `src/stackowl/config/settings.py`, add the import near the top (with the other `stackowl.config.*` imports):

```python
from stackowl.config.provider_tier_migration import migrate_legacy_tier_field
```

Update `_YamlSource._load()` (lines 53-70) — insert the migration call right before the read, so the subsequent `yaml.safe_load` reflects the migrated file:

```python
    def _load(self) -> dict[str, Any]:
        # Distinguish a MISSING file (legitimately {} — first-run defaults) from
        # an EXISTING file that fails to parse. A parse error of an existing file
        # must NOT fall back to {} (CFG-1 / F017): that silently emptied the
        # providers list to all-defaults and PASSED validation, the opposite of
        # the documented "previous settings kept" guarantee. Raise so a hot
        # reload is genuinely rejected (the watcher keeps the prior Settings) and
        # a boot-time typo fails loud instead of booting an empty config.
        if not self._path.exists():
            return {}
        # F-multi-tier — real, idempotent, comment-preserving one-time rewrite
        # of any legacy `tier:` provider entry to `tiers:`. This is the ONE
        # place every Settings() construction (orchestrator boot, every CLI
        # subcommand, MCP, identity CLI, hot-reload) already funnels through,
        # so no per-call-site migration hook is needed. Best-effort: a
        # migration failure is logged (inside migrate_legacy_tier_field
        # itself) and never blocks the read below — ProviderConfig's own
        # legacy-tier validator is the boot-safety backstop regardless.
        migrate_legacy_tier_field(self._path)
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("[config] Failed to parse %s: %s", self._path, exc)
            raise ConfigurationError(
                f"config file {self._path} exists but failed to parse: {exc}"
            ) from exc
        return raw if isinstance(raw, dict) else {}
```

Now fix the provider-summary log line at `settings.py:962` (search for `f"{p.name}[{p.tier}]"` — read the surrounding method first to confirm the exact current line, then replace):

```python
            names = ", ".join(f"{p.name}[{'/'.join(p.tiers)}]" for p in self.providers if p.enabled)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/config/test_settings_provider_migration.py tests/config/ -v`
Expected: PASS (both new tests, plus everything already in `tests/config/` from Tasks 1-2).

- [ ] **Step 5: Run the broader settings/provider-hot-reload regression suite**

Run: `uv run pytest tests/providers/test_provider_hot_reload.py -v`
Expected: at this point in the plan, 3 tests will FAIL (`registry._tiers["b"] == "standard"` etc. at lines 89/100/136 — these assert the registry's internal dict directly, which Task 4 hasn't updated yet). Confirm the failures are EXACTLY those 3 (not something else) — this is expected and will be fixed by Task 4, not this task.

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/stackowl/config/settings.py && uv run mypy src/stackowl/config/settings.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/config/settings.py tests/config/test_settings_provider_migration.py
git commit -m "feat(config): wire on-disk tier migration into Settings' YAML source"
```

---

### Task 4: `ProviderRegistry` — multi-tier membership everywhere

**Files:**
- Modify: `src/stackowl/providers/registry.py`
- Modify: `tests/providers/test_provider_hot_reload.py:89,100,136` (the 3 direct `_tiers` assertions flagged by Task 3 Step 5)

**Interfaces:**
- Consumes: `ProviderConfig.tiers` (Task 1).
- Produces: `ProviderRegistry._tiers: dict[str, tuple[str, ...]]`; `register_mock(..., tier: str = "fast", ...)` signature UNCHANGED (still accepts a single tier string for the ~98 existing test call sites) but stores `self._tiers[name] = (tier,)` internally.

- [ ] **Step 1: Write the failing tests**

Create `tests/providers/test_provider_registry_multi_tier_membership.py`:

```python
"""Tests for ProviderRegistry treating tier membership as containment
(a provider in multiple tiers), not equality."""

from __future__ import annotations

from stackowl.config.provider import ProviderConfig
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def test_register_mock_still_accepts_a_single_tier_string() -> None:
    """Regression: the ~98 existing register_mock(..., tier="fast") call
    sites across the test suite must keep working unchanged."""
    registry = ProviderRegistry()
    registry.register_mock("a", MockProvider(name="a"), tier="fast")
    assert registry.get_with_cascade("fast").name == "a"


def test_a_provider_registered_via_from_settings_with_multiple_tiers_is_selectable_from_both() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "standard"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    assert registry.get_with_cascade("fast").name == "groq"
    assert registry.get_with_cascade("standard").name == "groq"


def test_get_by_tier_finds_a_provider_present_in_a_non_primary_tier() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "powerful"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    assert registry.get_by_tier("powerful").name == "groq"


def test_resolve_capable_or_degrade_treats_multi_tier_membership_as_independent_per_tier() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "powerful"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    provider, degraded_from = registry.resolve_capable_or_degrade("powerful")
    assert provider.name == "groq"
    assert degraded_from is None  # exact match, not a substitution
```

(Check `MockProvider`'s and `ProviderConfig`'s exact constructor requirements first — read `src/stackowl/providers/mock_provider.py` if `MockProvider(name="a")` doesn't match its real signature; adjust accordingly.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier_membership.py -v`
Expected: FAIL — `ProviderConfig(tiers=(...))` construction succeeds (Task 1 shipped it), but registry methods still filter `t == tier` against a tuple, which is never equal to a string, so every multi-tier lookup returns nothing / falls through.

- [ ] **Step 3: Implement**

In `src/stackowl/providers/registry.py`:

Change the `_tiers` type annotation in `__init__` (line 94):
```python
        self._tiers: dict[str, tuple[str, ...]] = {}
```

Update `_build_into` (lines 160-203) — change the parameter type and the assignment:
```python
    def _build_into(
        self,
        config: ProviderConfig,
        *,
        clock: Clock,
        providers: dict[str, ModelProvider],
        tiers: dict[str, tuple[str, ...]],
        local: dict[str, bool],
        breakers: dict[str, CircuitBreaker],
        limiters: dict[str, RateLimiter],
        configs: dict[str, ProviderConfig],
        resolved_keys: dict[str, str],
    ) -> None:
```
and inside it, replace:
```python
        if hasattr(config, "tier") and config.tier:
            tiers[config.name] = config.tier
```
with:
```python
        if config.tiers:
            tiers[config.name] = config.tiers
```

Update `apply_settings`'s local var declaration and every `new_tiers[name] = self._tiers.get(name, config.tier)` occurrence (there are 3: in the secret-resolve-failure preserve branch, the fully-unchanged preserve branch, and the secret-rotation branch):
```python
            new_tiers: dict[str, tuple[str, ...]] = {}
```
and each of the 3 occurrences becomes:
```python
                        new_tiers[name] = self._tiers.get(name, config.tiers)
```

Update `get_by_tier` (lines 403-429) — change the filter:
```python
        for name, provider_tiers in tiers.items():
            if tier in provider_tiers and name in providers:
                return providers[name]
```

Update `get_with_cascade` (lines 431-545) — two occurrences of the containment filter:
```python
            tier_names = [name for name, t in tiers.items() if tier in t]
```
(replaces the line building `tier_names`) and:
```python
            candidates = [name for name, t in tiers.items() if tier in t and name in providers]
```
(replaces the `candidates` line near the end of the loop body).

Update `resolve_tier_with_fallback` (lines 547-589):
```python
        for name, ptiers in tiers.items():
            if tier in ptiers and name in providers:
                primary_name = name
                break
```

Update `resolve_capable_or_degrade` (lines 591-641) — two occurrences:
```python
        for name, provider_tiers in tiers.items():
            if tier in provider_tiers and name in providers:
                return providers[name], None
```
and:
```python
            for name, provider_tiers in tiers.items():
                if cand_tier in provider_tiers and name in providers:
```

Update `register_mock` (lines 684-710) — signature stays `tier: str = "fast"` (unchanged, for the ~98 existing call sites), only the storage line changes:
```python
        self._tiers[name] = (tier,)
```

- [ ] **Step 4: Fix the 3 flagged assertions in `test_provider_hot_reload.py`**

At line 89: `assert registry._tiers["b"] == "standard"` → `assert registry._tiers["b"] == ("standard",)`
At line 100: `assert "b" not in registry._tiers` — unchanged (membership-in-dict check, not a value comparison).
At line 136: `assert registry._tiers["a"] == "powerful"` → `assert registry._tiers["a"] == ("powerful",)`

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_provider_registry_multi_tier_membership.py tests/providers/test_provider_hot_reload.py tests/providers/test_provider_registry_multi_tier.py tests/providers/test_provider_registry_health.py -v`
Expected: PASS (all — including the pre-existing `test_provider_registry_multi_tier.py` from the earlier plan, which uses `register_mock(..., tier=...)` and must keep working byte-identically).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/registry.py && uv run mypy src/stackowl/providers/registry.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/providers/registry.py tests/providers/test_provider_registry_multi_tier_membership.py tests/providers/test_provider_hot_reload.py
git commit -m "feat(providers): ProviderRegistry treats tier membership as containment (multi-tier)"
```

---

### Task 5: `TierSelector` — containment check

**Files:**
- Modify: `src/stackowl/providers/tier_selector.py`
- Modify: `tests/providers/test_tier_selector.py` (all 5 fixtures use `dict[str, str]`, need `dict[str, tuple[str, ...]]`)

**Interfaces:**
- Consumes: nothing new — same `select()` signature, but the `tiers` parameter's value type changes.

- [ ] **Step 1: Update the test file's fixtures and write one new test**

Rewrite `tests/providers/test_tier_selector.py` in full:

```python
"""Tests for TierSelector — round-robin across healthy providers in a tier."""

from __future__ import annotations

from stackowl.infra.clock import WallClock
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.tier_selector import TierSelector


def _providers(*names: str) -> dict[str, object]:
    return {n: object() for n in names}


def test_round_robins_across_healthy_providers_in_tier() -> None:
    selector = TierSelector()
    providers = _providers("a", "b", "c")
    tiers = {"a": ("fast",), "b": ("fast",), "c": ("fast",)}
    breakers: dict[str, CircuitBreaker] = {}

    picks = [selector.select("fast", providers, tiers, breakers) for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]


def test_skips_open_breaker() -> None:
    selector = TierSelector()
    providers = _providers("a", "b")
    tiers = {"a": ("fast",), "b": ("fast",)}
    breaker_b = CircuitBreaker(provider_name="b", failure_threshold=1, clock=WallClock())
    breakers = {"b": breaker_b}
    # Force b OPEN.
    breaker_b._state = CircuitState.OPEN  # test-only direct state set
    breaker_b._opened_at = breaker_b._clock.monotonic()

    picks = [selector.select("fast", providers, tiers, breakers) for _ in range(3)]
    assert picks == ["a", "a", "a"]


def test_empty_tier_returns_none() -> None:
    selector = TierSelector()
    assert selector.select("powerful", {}, {}, {}) is None


def test_all_open_returns_none() -> None:
    selector = TierSelector()
    providers = _providers("a")
    tiers = {"a": ("fast",)}
    breaker_a = CircuitBreaker(provider_name="a", clock=WallClock())
    breaker_a._state = CircuitState.OPEN
    breaker_a._opened_at = breaker_a._clock.monotonic()
    breakers = {"a": breaker_a}

    assert selector.select("fast", providers, tiers, breakers) is None


def test_cursor_is_per_tier_independent() -> None:
    selector = TierSelector()
    providers = _providers("fast-a", "fast-b", "std-a")
    tiers = {"fast-a": ("fast",), "fast-b": ("fast",), "std-a": ("standard",)}
    breakers: dict = {}

    assert selector.select("fast", providers, tiers, breakers) == "fast-a"
    assert selector.select("standard", providers, tiers, breakers) == "std-a"
    assert selector.select("fast", providers, tiers, breakers) == "fast-b"


def test_a_provider_in_two_tiers_is_independently_selectable_from_both() -> None:
    """The core new capability: one provider present in BOTH tiers' pools,
    selectable from each tier's own round-robin independently."""
    selector = TierSelector()
    providers = _providers("multi", "fast-only")
    tiers = {"multi": ("fast", "standard"), "fast-only": ("fast",)}
    breakers: dict[str, CircuitBreaker] = {}

    assert selector.select("standard", providers, tiers, breakers) == "multi"
    # "fast" tier round-robins between "multi" and "fast-only" independently
    # of the "standard" pick above.
    fast_picks = {selector.select("fast", providers, tiers, breakers) for _ in range(2)}
    assert fast_picks == {"multi", "fast-only"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_tier_selector.py -v`
Expected: FAIL — `select()`'s current `t == tier` filter never matches a tuple against a string, so every test returns `None`/wrong picks.

- [ ] **Step 3: Implement**

In `src/stackowl/providers/tier_selector.py`, update the `select` method signature and filter:

```python
    def select(
        self,
        tier: str,
        providers: dict[str, object],
        tiers: dict[str, tuple[str, ...]],
        breakers: dict[str, CircuitBreaker],
    ) -> str | None:
        """Return the next healthy provider NAME for ``tier``, or None if empty/all-OPEN."""
        log.engine.debug("[tier_selector] select: entry", extra={"_fields": {"tier": tier}})
        candidates = [name for name, t in tiers.items() if tier in t and name in providers]
```

(The rest of the method body — `healthy`, cursor logic — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_tier_selector.py -v`
Expected: PASS (all 6, including the new multi-tier test).

- [ ] **Step 5: Run the registry regression suite again (TierSelector is its dependency)**

Run: `uv run pytest tests/providers/ -v`
Expected: PASS across the whole `tests/providers/` directory — this confirms Task 4 + Task 5 compose correctly.

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/tier_selector.py && uv run mypy src/stackowl/providers/tier_selector.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/providers/tier_selector.py tests/providers/test_tier_selector.py
git commit -m "feat(providers): TierSelector treats tier membership as containment (multi-tier)"
```

---

### Task 6: `RegistryAccessorsMixin.tier_of` → `tiers_of`

**Files:**
- Modify: `src/stackowl/providers/registry_accessors.py`
- Modify: `tests/vision/test_selector.py:92` (the only caller in the whole codebase — confirmed via grep during planning: no production code calls `tier_of`, only this one test)

**Interfaces:**
- Produces: `tiers_of(self, provider: ModelProvider) -> tuple[str, ...] | None` (renamed from `tier_of`, return type changed from `str | None`).

- [ ] **Step 1: Update the test**

In `tests/vision/test_selector.py`, change line 92:
```python
    assert reg.tier_of(reg.get("ollama")) == "fast"
```
to:
```python
    assert reg.tiers_of(reg.get("ollama")) == ("fast",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/vision/test_selector.py -v -k "tier"`
Expected: FAIL — `AttributeError: 'ProviderRegistry' object has no attribute 'tiers_of'` (or the test collecting normally but `tier_of` still returning a bare string not matching the tuple assertion — confirm which).

- [ ] **Step 3: Implement**

In `src/stackowl/providers/registry_accessors.py`, update the `_tiers` type annotation and rename the method:

```python
    _tiers: dict[str, tuple[str, ...]]
```

```python
    def tiers_of(self, provider: ModelProvider) -> tuple[str, ...] | None:
        """The configured routing tiers, or None if unknown."""
        name = self._name_of(provider)
        return self._tiers.get(name) if name is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/vision/test_selector.py -v`
Expected: PASS (the whole file — confirm no other test in it references the old `tier_of` name).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/providers/registry_accessors.py && uv run mypy src/stackowl/providers/registry_accessors.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/providers/registry_accessors.py tests/vision/test_selector.py
git commit -m "refactor(providers): rename RegistryAccessorsMixin.tier_of -> tiers_of (multi-tier)"
```

---

## Phase 2 — `/provider` command surface

### Task 7: `/provider list`/`menu`/`status` display every tier a provider belongs to

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Modify: `tests/commands/test_provider_command.py` (4 raw-dict fixtures at the lines identified below)

**Interfaces:**
- No new public interfaces — internal display logic only.

- [ ] **Step 1: Update the 4 raw-dict test fixtures first (TDD: these must fail against the OLD code before you touch it)**

In `tests/commands/test_provider_command.py`:

Line 99 (inside `test_list_shows_providers_without_raw_token`'s fixture dict) — change:
```python
                "tier": "fast",
```
to:
```python
                "tiers": ["fast"],
```

Line 134 (inside `test_list_shows_live_circuit_state`'s fixture dict) — same change: `"tier": "fast"` → `"tiers": ["fast"]`.

Lines 588 (inside `test_menu_shows_live_circuit_state`'s fixture dict) — same change.

Lines 621-622 (inside `test_status_subcommand_shows_all_providers_in_tier`'s fixture list) — change both:
```python
            {"name": "a", "protocol": "openai", "default_model": "m", "tier": "fast", "enabled": True},
            {"name": "b", "protocol": "openai", "default_model": "m", "tier": "fast", "enabled": True},
```
to:
```python
            {"name": "a", "protocol": "openai", "default_model": "m", "tiers": ["fast"], "enabled": True},
            {"name": "b", "protocol": "openai", "default_model": "m", "tiers": ["fast"], "enabled": True},
```

Also add one new test to `TestProviderStatus` (find that class in the file) proving multi-tier display:

```python
    @pytest.mark.asyncio
    async def test_list_shows_every_tier_a_provider_belongs_to(self, tmp_yaml: Path) -> None:
        data = _load(tmp_yaml)
        data["providers"] = [
            {"name": "multi", "protocol": "openai", "default_model": "m",
             "tiers": ["fast", "standard"], "enabled": True},
        ]
        tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")
        out = await _make_cmd().handle("list", _state())
        text = out.text if hasattr(out, "text") else out
        assert "fast" in text
        assert "standard" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "list or menu or status"`
Expected: FAIL on the 4 updated fixtures' tests (the command code still reads `p.get("tier")`, which is now absent, so `"fast"` never appears in the rendered text/badge lookups behave oddly) plus the new test.

- [ ] **Step 3: Implement**

In `src/stackowl/commands/provider_command.py`:

`_status` (around line 315-345) — change the filter and the tier display isn't needed here (status is already scoped to one tier), only the membership check changes:
```python
        names = [
            str(p.get("name"))
            for p in self._providers(data)
            if tier in (p.get("tiers") or []) and p.get("name")
        ]
```

`_list` (around line 349-382) — change the display line:
```python
        for p in providers:
            name = p.get("name", "?")
            protocol = p.get("protocol", "?")
            model = p.get("default_model", "?")
            tiers_display = ", ".join(p.get("tiers") or [])
            enabled = p.get("enabled", True)
            # Show ONLY the ref string — never resolve/print the actual secret.
            key_ref = p.get("api_key")
            key_disp = key_ref if key_ref else "(none)"
            lines.append(
                f"{name} | {protocol} | {model} | {tiers_display} | "
                f"enabled={enabled} | api_key={key_disp}{self._live_status_badge(name)}"
            )
```

`_menu` (around line 386-431) — change the display line and the "Set tier" button filter:
```python
        protocol = target.get("protocol", "?")
        model = target.get("default_model", "?")
        current_tiers = target.get("tiers") or []
        tiers_display = ", ".join(current_tiers)
        enabled = target.get("enabled", True)
        text = f"{name} | {protocol} | {model} | {tiers_display} | enabled={enabled}{self._live_status_badge(name)}"
        toggle_verb = "disable" if enabled else "enable"
        actions = (
            tuple(
                Action(
                    label=f"Add tier: {t}",
                    command=f"/provider set-tier {name} {t}",
                    destructive=False,
                )
                for t in _VALID_TIERS
                if t not in current_tiers
            )
            + (
```
(the rest of the tuple concatenation — Edit/toggle/Remove actions — is unchanged, only the `tuple(...)` comprehension's filter and each `Action`'s label text changed from `"Set tier: {t}"` to `"Add tier: {t}"` to match the now-additive semantics.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "list or menu or status"`
Expected: PASS.

- [ ] **Step 5: Run the full file's suite (broader regression)**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Expected: some failures remain — `TestProviderAdd`/`TestProviderSetTier` tests not yet updated (Tasks 8-9 handle those). Confirm the failures are ONLY in those two test classes, nothing else.

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py && uv run mypy src/stackowl/commands/provider_command.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): /provider list/menu/status display every tier a provider belongs to"
```

---

### Task 8: `/provider set-tier` becomes additive (adds a tier, never replaces)

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Modify: `tests/commands/test_provider_command.py` (`TestProviderSetTier` class, lines ~896-919)

**Interfaces:**
- No signature change — `_set_tier(self, raw: str) -> str` behavior changes.

- [ ] **Step 1: Update the tests**

In `tests/commands/test_provider_command.py`, replace `test_set_tier_valid` and `test_set_tier_invalid`:

```python
    @pytest.mark.asyncio
    async def test_set_tier_valid(self, tmp_yaml: Path) -> None:
        bus = _SpyBus()
        await _make_cmd().handle("add acme openai gpt-x fast", _state())
        out = await _make_cmd(bus).handle("set-tier acme powerful", _state())
        assert "✓" in out or "powerful" in out
        entry = next(p for p in _load(tmp_yaml)["providers"] if p["name"] == "acme")
        assert entry["tiers"] == ["fast", "powerful"]
        assert any(e == "settings_reloaded" for e, _ in bus.events)

    @pytest.mark.asyncio
    async def test_set_tier_is_idempotent_on_an_already_assigned_tier(self, tmp_yaml: Path) -> None:
        await _make_cmd().handle("add acme openai gpt-x fast", _state())
        out = await _make_cmd().handle("set-tier acme fast", _state())
        assert "already" in out.lower()
        entry = next(p for p in _load(tmp_yaml)["providers"] if p["name"] == "acme")
        assert entry["tiers"] == ["fast"]

    @pytest.mark.asyncio
    async def test_set_tier_invalid(self, tmp_yaml: Path) -> None:
        await _make_cmd().handle("add acme openai gpt-x fast", _state())
        out = await _make_cmd().handle("set-tier acme turbo", _state())
        assert "✗" in out or "invalid" in out.lower()
        entry = next(p for p in _load(tmp_yaml)["providers"] if p["name"] == "acme")
        assert entry["tiers"] == ["fast"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "set_tier"`
Expected: FAIL — `_set_tier` still does `target["tier"] = tier` (replace), so `entry["tiers"]` doesn't exist and the idempotent-add test's "already" message doesn't exist either.

- [ ] **Step 3: Implement**

In `src/stackowl/commands/provider_command.py`, replace `_set_tier`'s body (around line 1157-1188) from `target = next(...)` onward:

```python
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.config.warning(
                "[commands] provider.set_tier: not found",
                extra={"_fields": {"name": name}},
            )
            return f"✗ Provider '{name}' not found"
        current_tiers = target.get("tiers") or []
        if tier in current_tiers:
            log.config.debug(
                "[commands] provider.set_tier: exit — already in tier",
                extra={"_fields": {"name": name, "tier": tier}},
            )
            return f"✓ Provider '{name}' is already in tier '{tier}'"
        target["tiers"] = [*current_tiers, tier]
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.set_tier: exit — updated",
            extra={"_fields": {"name": name, "tier": tier, "tiers": target["tiers"]}},
        )
        return f"✓ Provider '{name}' added to tier '{tier}' — applied immediately"
```

Also update the `SubCommand` description for `set-tier` in `_PROVIDER_META` (around line 114-126) so the help text matches the new semantics:
```python
        SubCommand(
            name="set-tier",
            summary="Add a tier to a provider's routing membership",
            description=(
                "You add this tier to the provider's tier list (it keeps any "
                "tiers it already had — this never replaces). The change "
                "applies immediately."
            ),
            args=(
                Arg(name="name", summary="provider to add a tier to"),
                Arg(name="tier", summary="tier to add", choices=_VALID_TIERS),
            ),
            examples=(Example(invocation="/provider set-tier openai powerful"),),
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "set_tier"`
Expected: PASS (all 3).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py && uv run mypy src/stackowl/commands/provider_command.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): /provider set-tier adds a tier instead of replacing (multi-tier)"
```

---

### Task 9: `/provider add` (positional) + guided `add-tier` write `tiers` list

**Files:**
- Modify: `src/stackowl/commands/provider_command.py`
- Modify: `tests/commands/test_provider_command.py` (3 assertions: line 188, 459, and the `TestProviderAdd`/add-tier tests)

**Interfaces:**
- No signature changes — the `entry: dict[str, Any]` built by `_add` and `_add_tier` gains `"tiers": [tier]` instead of `"tier": tier`.

- [ ] **Step 1: Update the tests**

In `tests/commands/test_provider_command.py`:

Line 188: `assert entry["tier"] == "fast"` → `assert entry["tiers"] == ["fast"]`
Line 459: `assert saved["tier"] == "fast"` → `assert saved["tiers"] == ["fast"]`

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_provider_command.py -v -k "add"`
Expected: FAIL on those two assertions (plus anything already broken from prior tasks not yet reached — ignore those, they're covered by their own tasks).

- [ ] **Step 3: Implement**

In `src/stackowl/commands/provider_command.py`, `_add` (around line 1038-1122) — change the `entry` dict construction:
```python
        entry: dict[str, Any] = {
            "name": name,
            "protocol": protocol,
            "enabled": True,
            "api_key": None,
            "base_url": base_url,
            "default_model": default_model,
            "tiers": [tier],
        }
```

`_add_tier` (around line 912-974) — change the `provider_entry` dict construction:
```python
        provider_entry: dict[str, Any] = {
            "name": name,
            "protocol": entry.protocol,
            "enabled": True,
            "api_key": None if api_key_ref == "-" else api_key_ref,
            "base_url": entry.base_url or None,
            "default_model": model,
            "tiers": [tier],
        }
```

`_persist_new_provider`'s log line (around line 1029-1032) — change the field name it logs:
```python
        log.config.debug(
            "[commands] provider.persist_new_provider: exit — added",
            extra={"_fields": {"name": name, "protocol": entry.get("protocol"), "tiers": entry.get("tiers")}},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Expected: PASS — the ENTIRE file should now be green (this is the last task touching `test_provider_command.py`'s tier-related assertions).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/provider_command.py && uv run mypy src/stackowl/commands/provider_command.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "feat(commands): /provider add + guided add-tier write tiers list"
```

---

## Phase 3 — `/tier` command surface

### Task 10: `/tier` admin subcommands — containment checks + additive/subtractive list semantics

**Files:**
- Modify: `src/stackowl/commands/tier_command.py`
- Rewrite: `tests/commands/test_tier_admin.py` (full file — every fixture and several assertions change; the file is reproduced in full below)

**Interfaces:**
- No signature changes to any public method — internal list-membership logic changes throughout `_list`, `_menu`, `_add_browse`, `_add_execute`, `_remove_browse`, `_remove_execute`.

- [ ] **Step 1: Replace `tests/commands/test_tier_admin.py` in full**

This is the complete new file (write it verbatim — every fixture now uses `"tiers": [...]`, and the add/remove tests assert the new list-mutation semantics):

```python
"""Tests for /tier's provider-tier membership admin job (list/menu/add/remove).

The session-preference job (bare tier name) is covered by
tests/journeys/commands/test_tier_command.py and is untouched by this file —
these tests are scoped to the admin subcommands merged in alongside it (see
tier_command.py's module docstring for why the two share one command).
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
                        "tiers": ["fast"],
                        "enabled": True,
                    },
                    {
                        "name": "openai",
                        "protocol": "openai",
                        "default_model": "gpt-4o",
                        "tiers": ["powerful"],
                        "enabled": True,
                    },
                    {
                        "name": "disabled-one",
                        "protocol": "openai",
                        "default_model": "m",
                        "tiers": ["fast"],
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

    @pytest.mark.asyncio
    async def test_list_shows_a_provider_under_every_tier_it_belongs_to(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "stackowl.yaml"
        cfg.write_text(
            yaml.dump({"test_mode": True, "providers": [
                {"name": "multi", "protocol": "openai", "default_model": "m",
                 "tiers": ["fast", "standard"], "enabled": True},
            ]}),
            encoding="utf-8",
        )
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
        reply = await _make_cmd().handle("list", _state())
        assert isinstance(reply, CommandResponse)
        # "multi" appears once under "fast:" and once under "standard:".
        assert reply.text.count("multi") == 2


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
    async def test_add_browse_shows_candidates_not_already_in_this_tier(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add powerful", _state())
        assert isinstance(reply, CommandResponse)
        # groq (fast) and disabled-one (fast) are candidates; openai is already powerful.
        assert any(a.command == "/tier add powerful groq" for a in reply.actions)
        assert not any("/tier add powerful openai" == a.command for a in reply.actions)

    @pytest.mark.asyncio
    async def test_add_execute_appends_tier_without_removing_existing_ones(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add powerful groq", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        assert "powerful" in reply
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        assert groq["tiers"] == ["fast", "powerful"]

    @pytest.mark.asyncio
    async def test_add_execute_already_in_tier(self, tmp_yaml: Path) -> None:
        reply = await _make_cmd().handle("add fast groq", _state())
        assert isinstance(reply, str)
        assert "already in tier" in reply
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        assert groq["tiers"] == ["fast"]  # unchanged, not duplicated

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
                {"name": "solo", "protocol": "openai", "default_model": "m",
                 "tiers": ["fast"], "enabled": True},
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
    async def test_remove_execute_on_a_providers_only_tier_disables_without_emptying_tiers(
        self, tmp_yaml: Path
    ) -> None:
        reply = await _make_cmd().handle("remove fast groq", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        assert "disabled" in reply
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        assert groq["enabled"] is False
        # groq's ONLY tier was "fast" — never write an empty tiers list.
        assert groq["tiers"] == ["fast"]

    @pytest.mark.asyncio
    async def test_remove_execute_on_one_of_several_tiers_leaves_provider_enabled(
        self, tmp_yaml: Path
    ) -> None:
        # Give groq a second tier first.
        await _make_cmd().handle("add powerful groq", _state())
        reply = await _make_cmd().handle("remove fast groq", _state())
        assert isinstance(reply, str)
        assert reply.startswith("✓")
        data = _load(tmp_yaml)
        groq = next(p for p in data["providers"] if p["name"] == "groq")
        # Still enabled — "powerful" remains, "fast" was removed (not disabled).
        assert groq["enabled"] is True
        assert groq["tiers"] == ["powerful"]

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
        admin dashboard. Text wording must stay unchanged; buttons are new."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_tier_admin.py -v`
Expected: FAIL — most tests, since `tier_command.py`'s `_list`/`_menu`/`_add_browse`/`_add_execute`/`_remove_browse`/`_remove_execute` all still read/write the singular `"tier"` key.

- [ ] **Step 3: Implement**

In `src/stackowl/commands/tier_command.py`:

`_list` (around line 268-290) — change the membership filter and display:
```python
        for tier in _VALID_TIERS:
            members = [p for p in providers if tier in (p.get("tiers") or [])]
```

`_menu` (around line 294-322) — same filter change:
```python
        members = [p for p in providers if tier in (p.get("tiers") or [])]
```

`_add_browse` (around line 326-357) — change the "not already a member" filter:
```python
        candidates = [p for p in providers if tier not in (p.get("tiers") or [])]
```
and update the label (which currently shows `"{name} (from {tier})"` assuming one tier — change to show ALL current tiers):
```python
        actions = tuple(
            Action(
                label=f"{p.get('name', '?')} (currently: {', '.join(p.get('tiers') or [])})",
                command=f"/tier add {tier} {p.get('name', '?')}",
                destructive=False,
            )
            for p in candidates
        )
```

`_add_execute` (around line 359-391) — change from overwrite to append:
```python
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.engine.warning("[commands] tier.add_execute: not found", extra={"_fields": {"name": name}})
            return f"✗ Provider '{name}' not found"
        current_tiers = target.get("tiers") or []
        if tier in current_tiers:
            log.engine.debug(
                "[commands] tier.add_execute: exit — already in tier",
                extra={"_fields": {"name": name, "tier": tier}},
            )
            return f"✓ Provider '{name}' is already in tier '{tier}'"
        target["tiers"] = [*current_tiers, tier]
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.engine.info(
            "[commands] tier.add_execute: exit — updated", extra={"_fields": {"name": name, "tier": tier}}
        )
        return f"✓ Provider '{name}' added to tier '{tier}' — applied immediately"
```

`_remove_browse` (around line 395-423) — change the candidate filter:
```python
        candidates = [
            p for p in providers
            if tier in (p.get("tiers") or []) and p.get("enabled", True)
        ]
```

`_remove_execute` (around line 425-460) — this is the method with real new logic (subtract-from-list, disable-without-emptying):
```python
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.engine.warning(
                "[commands] tier.remove_execute: not found", extra={"_fields": {"name": name}}
            )
            return f"✗ Provider '{name}' not found"
        current_tiers = target.get("tiers") or []
        if tier not in current_tiers:
            log.engine.debug(
                "[commands] tier.remove_execute: exit — not in this tier",
                extra={"_fields": {"name": name, "tier": tier, "current_tiers": current_tiers}},
            )
            return f"✗ Provider '{name}' is not in tier '{tier}' (it's in {current_tiers})"
        if len(current_tiers) == 1:
            # This is the provider's ONLY tier — disable rather than write an
            # empty tiers list (the schema requires at least one entry, and
            # this preserves the "always routable-or-explicitly-off" invariant).
            target["enabled"] = False
        else:
            target["tiers"] = [t for t in current_tiers if t != tier]
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.engine.info(
            "[commands] tier.remove_execute: exit — updated",
            extra={"_fields": {"name": name, "tier": tier, "disabled": len(current_tiers) == 1}},
        )
        return f"✓ Provider '{name}' removed from tier '{tier}' (disabled) — applied immediately"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_tier_admin.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/commands/tier_command.py && uv run mypy src/stackowl/commands/tier_command.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/tier_command.py tests/commands/test_tier_admin.py
git commit -m "feat(commands): /tier add/remove mutate the provider's tiers list (multi-tier)"
```

---

## Phase 4 — CLI surface (`stackowl providers ...`)

### Task 11: `cli/providers_cli.py` + `setup/yaml_writer.py` — multi-tier aware

**Files:**
- Modify: `src/stackowl/cli/providers_cli.py`
- Modify: `src/stackowl/setup/yaml_writer.py`
- Modify: `tests/cli/test_providers_cli.py` (`test_providers_edit_changes_tier`)

**Interfaces:**
- No signature changes to any Typer command.

- [ ] **Step 1: Update the test**

In `tests/cli/test_providers_cli.py`, in `test_providers_edit_changes_tier` (around line 217-240), change:
```python
    assert ollama["tier"] == "standard"
```
to:
```python
    assert ollama["tiers"] == ["standard"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_providers_cli.py -v -k test_providers_edit_changes_tier`
Expected: FAIL — `update_provider_field` still writes the `"tier"` key.

- [ ] **Step 3: Implement**

In `src/stackowl/setup/yaml_writer.py`, `write_provider_config` (around line 51-61) — change the single write site:
```python
    new_entry: dict[str, Any] = {
        "name": entry.name,
        "protocol": entry.protocol,
        "enabled": True,
        "api_key": api_key_ref or None,
    }
    if base_url:
        new_entry["base_url"] = base_url
    if default_model:
        new_entry["default_model"] = default_model
    new_entry["tiers"] = [entry.tier]
```

In `src/stackowl/cli/providers_cli.py`:

`providers_list` (around line 106-109) — change the display:
```python
    typer.echo(f"  {'NAME':<20} {'PROTOCOL':<12} {'TIERS':<20} STATUS")
    for prov in settings.providers:
        status = "enabled" if prov.enabled else "disabled"
        typer.echo(f"  {prov.name:<20} {prov.protocol:<12} {','.join(prov.tiers):<20} {status}")
```

`providers_add` (around line 220, 249-250) — the catalog `entry.tier` (singular, `ProviderEntry`) is unaffected; only the write-if-changed call needs the new field name:
```python
    if tier != entry.tier:
        update_provider_field(config_path, entry.name, "tiers", [tier])
```

`providers_remove` (line 279) — the raw-dict display doesn't affect any assertion (confirmed during planning — no test checks this line's exact tier text) but update it for correctness/consistency:
```python
    typer.echo(f"  {name}  tiers={raw.get('tiers', ['?'])}  base_url={raw.get('base_url', '?')}")
```

`providers_edit` (around line 313-345) — this is a single-choice Typer prompt (`_choose_tier` returns exactly one tier), so its "change tier" action means REPLACE (not the multi-select add/remove semantics the slash commands have — a reasonable simplification for a single-value CLI prompt, not a redesign):
```python
    prov = next((p for p in settings.providers if p.name == name), None)
    if prov is None:
        typer.echo(f"✗ Provider not found: {name}", err=True)
        raise typer.Exit(1)

    current_tier = prov.tiers[0]
    typer.echo(f"\n  Editing: {name}")
    typer.echo(f"  tiers={list(prov.tiers)}  base_url={prov.base_url or '(none)'}  "
               f"model={prov.default_model}  rate_limit={prov.rate_limit_rpm or 'unlimited'}")

    new_tier = _choose_tier(default=current_tier)
    new_base_url = typer.prompt("Base URL", default=prov.base_url or "").strip()
    new_model = typer.prompt("Default model", default=prov.default_model).strip()
    raw_limit = typer.prompt(
        "Rate limit rpm (0 = unlimited)", default=str(prov.rate_limit_rpm or 0)
    ).strip()
    try:
        new_rate_limit: int | None = int(raw_limit) or None
    except ValueError:
        new_rate_limit = prov.rate_limit_rpm

    changed: list[str] = []
    if new_tier != current_tier:
        update_provider_field(config_path, name, "tiers", [new_tier])
        changed.append("tiers")
```
(the remaining `if new_base_url != ...` / `if new_model ...` / `if new_rate_limit ...` blocks below are unchanged — only the tier comparison/write above changes; read the surrounding method first to confirm you're only touching what's shown here.)

`providers_test` (around line 384-393) — constructing a `ProviderEntry` (catalog, singular) from a `ProviderConfig` (now plural) for a connectivity smoke test — use the first/primary tier as a sensible representative (the test doesn't care which tier, only that connectivity works):
```python
    test_entry = ProviderEntry(
        name=prov.name,
        label=prov.name,
        protocol=prov.protocol,
        base_url=prov.base_url or "",
        default_model=prov.default_model,
        models=(),
        tier=prov.tiers[0],
        needs_api_key=bool(prov.api_key),
        is_local=not bool(prov.api_key),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_providers_cli.py -v`
Expected: PASS (all — this exercises `providers_list`/`add`/`remove`/`edit`/`test`, so a full pass here is a strong regression signal for the whole CLI surface).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/cli/providers_cli.py src/stackowl/setup/yaml_writer.py && uv run mypy src/stackowl/cli/providers_cli.py src/stackowl/setup/yaml_writer.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/cli/providers_cli.py src/stackowl/setup/yaml_writer.py tests/cli/test_providers_cli.py
git commit -m "feat(cli): stackowl providers add/edit/list/test are multi-tier aware"
```

---

## Phase 5 — Integration & regression

### Task 12: Fix the 2 remaining journey/button-chain assertions + full guided-add-flow smoke

**Files:**
- Modify: `tests/journeys/commands/test_provider_command_journey.py:260`
- Modify: `tests/channels/telegram/test_command_buttons.py:494`

**Interfaces:**
- None — pure test-assertion fixes.

- [ ] **Step 1: Update both assertions**

In `tests/journeys/commands/test_provider_command_journey.py`, line 260:
```python
    assert persisted["tier"] == tier
```
→
```python
    assert persisted["tiers"] == [tier]
```

In `tests/channels/telegram/test_command_buttons.py`, line 494:
```python
    assert persisted["tier"] == tier
```
→
```python
    assert persisted["tiers"] == [tier]
```

- [ ] **Step 2: Run both files to verify they fail, then pass**

Run: `uv run pytest tests/journeys/commands/test_provider_command_journey.py tests/channels/telegram/test_command_buttons.py -v`
Expected: FAIL first (against the pre-Task-9 code — but by this point in the plan Task 9 already shipped, so this should actually PASS immediately; if it fails, something in Tasks 7-9 was missed — investigate before proceeding, don't paper over it). Confirm PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/journeys/commands/test_provider_command_journey.py tests/channels/telegram/test_command_buttons.py
git commit -m "test: fix guided add-flow journey/button-chain assertions for tiers list"
```

---

### Task 13: New integration test — a provider in two tiers, added via `/tier add` twice, routable from both

**Files:**
- Modify: `tests/journeys/commands/test_provider_command_journey.py` (add one new test)

**Interfaces:**
- Consumes: everything from Phases 1-3.

- [ ] **Step 1: Read the existing file's fixtures/imports first**

Read `tests/journeys/commands/test_provider_command_journey.py` in full (it already exists and has established `tmp_yaml`/`register_all_commands`/`CommandRegistry` conventions from the prior multi-provider-catalog plan) before writing the new test, so it matches the file's exact style.

- [ ] **Step 2: Write the new test**

Add to the file:

```python
async def test_provider_added_to_two_tiers_via_tier_add_is_routable_from_both(
    tmp_yaml: Path,
) -> None:
    """End-to-end proof of the core new capability: a provider configured
    once, added to a SECOND tier via /tier add, ends up in BOTH tiers' live
    routing pools — not just persisted twice under two different names."""
    from stackowl.config.settings import Settings
    from stackowl.providers.registry import ProviderRegistry

    deps = CommandDeps(event_bus=MagicMock())
    register_all_commands(deps, registry=CommandRegistry.instance())
    registry = CommandRegistry.instance()

    add_reply = await registry.dispatch(
        "provider", "add multiprov openai gpt-4o fast", make_state()
    )
    assert "✓" in add_reply.text

    tier_reply = await registry.dispatch("tier", "add standard multiprov", make_state())
    assert "✓" in tier_reply.text
    assert "standard" in tier_reply.text

    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    persisted = next(p for p in data["providers"] if p["name"] == "multiprov")
    assert persisted["tiers"] == ["fast", "standard"]

    live_registry = ProviderRegistry.from_settings(Settings())
    assert live_registry.get_with_cascade("fast").name == "multiprov"
    assert live_registry.get_with_cascade("standard").name == "multiprov"
```

(Match the exact import style/`CommandDeps`/`MagicMock`/`make_state` usage already present at the top of the file — if any of these names differ from what's shown here, use the file's REAL names, discovered in Step 1.)

- [ ] **Step 3: Run to verify it fails, then implement any gap it surfaces**

Run: `uv run pytest tests/journeys/commands/test_provider_command_journey.py -v -k test_provider_added_to_two_tiers`

If it fails for a reason NOT explained by "this exact test doesn't exist yet" (e.g. a DI wiring gap between `/provider` and `/tier` sharing the same `ProviderRegistry` instance in this test's harness), fix that specific gap — do not loosen the test.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/journeys/commands/test_provider_command_journey.py -v`
Expected: PASS (the whole file).

- [ ] **Step 5: Commit**

```bash
git add tests/journeys/commands/test_provider_command_journey.py
git commit -m "test: end-to-end proof a provider added to two tiers is routable from both"
```

---

### Task 14: Final full regression pass

**Files:**
- None modified — verification only.

- [ ] **Step 1: Run every test file this plan touched or is downstream of, in one pass**

Run:
```bash
uv run pytest \
  tests/config/ \
  tests/providers/ \
  tests/commands/test_provider_command.py \
  tests/commands/test_tier_admin.py \
  tests/commands/test_tier_meta.py \
  tests/journeys/commands/test_provider_command_journey.py \
  tests/journeys/commands/test_tier_command.py \
  tests/channels/telegram/test_command_buttons.py \
  tests/vision/test_selector.py \
  tests/cli/test_providers_cli.py \
  tests/setup/test_provider_catalog.py \
  -v
```
Expected: 100% pass. If anything fails, it's either a gap this plan's task list missed (fix it, tracing back to which task's change caused it) or a genuinely pre-existing unrelated failure (confirm via `git stash` + re-run on the pre-plan commit before concluding that).

- [ ] **Step 2: Lint + type-check every touched file in one pass**

Run:
```bash
uv run ruff check \
  src/stackowl/config/provider.py \
  src/stackowl/config/provider_tier_migration.py \
  src/stackowl/config/settings.py \
  src/stackowl/providers/registry.py \
  src/stackowl/providers/tier_selector.py \
  src/stackowl/providers/registry_accessors.py \
  src/stackowl/commands/provider_command.py \
  src/stackowl/commands/tier_command.py \
  src/stackowl/cli/providers_cli.py \
  src/stackowl/setup/yaml_writer.py

uv run mypy \
  src/stackowl/config/provider.py \
  src/stackowl/config/provider_tier_migration.py \
  src/stackowl/config/settings.py \
  src/stackowl/providers/registry.py \
  src/stackowl/providers/tier_selector.py \
  src/stackowl/providers/registry_accessors.py \
  src/stackowl/commands/provider_command.py \
  src/stackowl/commands/tier_command.py \
  src/stackowl/cli/providers_cli.py \
  src/stackowl/setup/yaml_writer.py
```
Expected: no errors in any of these files (the repo may have pre-existing errors elsewhere — not in scope).

- [ ] **Step 3: Manual smoke check of the live platform (if running)**

If a StackOwl instance is currently running, this is a good point to restart it and manually exercise `/provider add`, `/provider set-tier <name> <tier>` twice on the same provider, `/tier add <other-tier> <name>`, `/tier list`, and `/tier remove <tier> <name>` via Telegram or the TUI to confirm the button-driven flow feels right end-to-end — this is a real schema/behavior change touching two live command surfaces, not just internal refactoring.

- [ ] **Step 4: No commit needed for this task** — it's pure verification. If Step 1 or Step 2 surfaced a real gap, fix it as part of the task that should have covered it (amend that task's commit is NOT appropriate per this project's git discipline — create a small follow-up commit instead, clearly noting what was missed).
