"""YamlWriter — merge-write provider and channel config into stackowl.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.setup.provider_catalog import ProviderEntry

__all__ = ["remove_provider_config", "update_provider_field", "write_channel_config", "write_provider_config"]


def write_provider_config(
    config_path: Path,
    entry: "ProviderEntry",
    api_key_ref: str,
    *,
    base_url_override: str | None = None,
    default_model_override: str | None = None,
) -> None:
    """Merge a provider entry into stackowl.yaml, preserving existing content.

    Uses ruamel.yaml so comments and other keys are left intact.
    Creates the file if it does not exist.
    """
    # 1. ENTRY
    log.setup.debug(
        "[yaml_writer] write_provider_config: entry",
        extra={"_fields": {"config_path": str(config_path), "provider": entry.name}},
    )
    yml = _make_yaml()

    # 2. DECISION — load existing content or start fresh
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = yml.load(fh) or {}
        log.setup.debug("[yaml_writer] write_provider_config: decision — merging into existing config")
    else:
        data = {}
        log.setup.debug("[yaml_writer] write_provider_config: decision — creating new config")

    # 3. STEP — build provider entry from ProviderEntry fields
    base_url = base_url_override or entry.base_url or None
    default_model = default_model_override or entry.default_model or None

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

    providers_list: list[Any] = data.get("providers") or []
    if not isinstance(providers_list, list):
        providers_list = []
    data["providers"] = providers_list

    replaced = False
    for i, existing in enumerate(providers_list):
        if isinstance(existing, dict) and existing.get("name") == entry.name:
            providers_list[i] = new_entry
            replaced = True
            break
    if not replaced:
        providers_list.append(new_entry)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        yml.dump(data, fh)

    # 4. EXIT
    log.setup.info(
        "[yaml_writer] write_provider_config: exit — config written",
        extra={"_fields": {"config_path": str(config_path), "provider": entry.name}},
    )


def write_channel_config(
    config_path: Path,
    channel_key: str,
    channel_data: dict[str, Any],
) -> None:
    """Merge a channel section into stackowl.yaml without clobbering other keys.

    ``channel_key`` is the top-level key in the YAML file (e.g. ``telegram_channel``).
    ``channel_data`` is merged into any existing value for that key.
    """
    # 1. ENTRY
    log.setup.debug(
        "[yaml_writer] write_channel_config: entry",
        extra={"_fields": {"config_path": str(config_path), "channel_key": channel_key}},
    )
    yml = _make_yaml()

    # 2. DECISION — load existing content or start fresh
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = yml.load(fh) or {}
        log.setup.debug("[yaml_writer] write_channel_config: decision — merging into existing config")
    else:
        data = {}
        log.setup.debug("[yaml_writer] write_channel_config: decision — creating new config")

    # 3. STEP — merge channel data (new values win over existing)
    existing_channel: dict[str, Any] = data.get(channel_key) or {}
    if not isinstance(existing_channel, dict):
        existing_channel = {}
    merged = {**existing_channel, **channel_data}
    data[channel_key] = merged

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        yml.dump(data, fh)

    # 4. EXIT
    log.setup.info(
        "[yaml_writer] write_channel_config: exit — channel config written",
        extra={"_fields": {"config_path": str(config_path), "channel_key": channel_key}},
    )


def remove_provider_config(config_path: Path, name: str) -> bool:
    """Pop the named provider from stackowl.yaml. Returns True if removed."""
    # 1. ENTRY
    log.setup.debug(
        "[yaml_writer] remove_provider_config: entry",
        extra={"_fields": {"config_path": str(config_path), "provider": name}},
    )
    if not config_path.exists():
        log.setup.debug("[yaml_writer] remove_provider_config: decision — config not found, nothing to remove")
        return False
    yml = _make_yaml()

    # 2. DECISION — load and locate entry
    with config_path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = yml.load(fh) or {}
    providers_list: list[Any] = data.get("providers") or []
    if not isinstance(providers_list, list):
        log.setup.debug("[yaml_writer] remove_provider_config: decision — no providers list")
        return False

    # 3. STEP — find and remove
    original_len = len(providers_list)
    data["providers"] = [
        e for e in providers_list
        if not (isinstance(e, dict) and e.get("name") == name)
    ]
    if len(data["providers"]) == original_len:
        log.setup.debug(
            "[yaml_writer] remove_provider_config: decision — provider not found",
            extra={"_fields": {"provider": name}},
        )
        return False

    with config_path.open("w", encoding="utf-8") as fh:
        yml.dump(data, fh)

    # 4. EXIT
    log.setup.info(
        "[yaml_writer] remove_provider_config: exit — removed",
        extra={"_fields": {"config_path": str(config_path), "provider": name}},
    )
    return True


def update_provider_field(config_path: Path, name: str, field: str, value: Any) -> bool:
    """Set one field on the named provider entry. Returns True if updated."""
    # 1. ENTRY
    log.setup.debug(
        "[yaml_writer] update_provider_field: entry",
        extra={"_fields": {"config_path": str(config_path), "provider": name, "field": field}},
    )
    if not config_path.exists():
        log.setup.debug("[yaml_writer] update_provider_field: decision — config not found")
        return False
    yml = _make_yaml()

    # 2. DECISION — load and locate entry
    with config_path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = yml.load(fh) or {}
    providers_list: list[Any] = data.get("providers") or []
    if not isinstance(providers_list, list):
        log.setup.debug("[yaml_writer] update_provider_field: decision — no providers list")
        return False

    # 3. STEP — update field in-place
    found = False
    for entry in providers_list:
        if isinstance(entry, dict) and entry.get("name") == name:
            entry[field] = value
            found = True
            break
    if not found:
        log.setup.debug(
            "[yaml_writer] update_provider_field: decision — provider not found",
            extra={"_fields": {"provider": name}},
        )
        return False

    with config_path.open("w", encoding="utf-8") as fh:
        yml.dump(data, fh)

    # 4. EXIT
    log.setup.info(
        "[yaml_writer] update_provider_field: exit — updated",
        extra={"_fields": {"config_path": str(config_path), "provider": name, "field": field}},
    )
    return True


def _make_yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y
