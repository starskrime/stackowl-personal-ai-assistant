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

    try:
        with path.open("w", encoding="utf-8") as fh:
            yml.dump(data, fh)
    except Exception as exc:
        log.config.warning(
            "[config] provider_tier_migration.migrate: exit — write failed, leaving untouched",
            extra={"_fields": {"path": str(path), "error": str(exc)}},
        )
        return False
    log.config.info(
        "[config] provider_tier_migration.migrate: exit — rewrote legacy tier field",
        extra={"_fields": {"path": str(path), "providers_migrated": migrated}},
    )
    return True
