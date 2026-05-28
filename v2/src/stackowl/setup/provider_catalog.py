"""ProviderCatalog — extensible registry of concrete AI providers.

Each provider declares which of the four base protocols it speaks
(anthropic | openai | gemini | grok) so the rest of the system never
needs to branch on provider names.

Bundled definitions live in src/stackowl/setup/providers/*.yaml.
User overrides live in ~/.stackowl/providers/*.yaml — a file with the
same ``name`` field replaces the bundled entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any  # noqa: F401 (used in TYPE_CHECKING-style annotations)

import yaml

from stackowl.infra.observability import log

__all__ = ["PROTOCOLS", "ProviderCatalog", "ProviderEntry"]

PROTOCOLS: tuple[str, ...] = ("anthropic", "openai", "gemini", "grok")

_BUNDLED_DIR = Path(__file__).parent / "providers"

_PROTOCOL_ORDER = {p: i for i, p in enumerate(("anthropic", "gemini", "grok", "openai"))}
_PROTOCOL_LABEL = {
    "anthropic": "Anthropic-compatible",
    "openai": "OpenAI-compatible",
    "gemini": "Google Gemini",
    "grok": "xAI Grok",
}


@dataclass(frozen=True)
class ProviderEntry:
    """One AI provider the user can pick during onboarding."""

    name: str
    label: str
    protocol: str
    base_url: str
    default_model: str
    models: tuple[str, ...] = field(default_factory=tuple)
    tier: str = "powerful"
    needs_api_key: bool = True
    is_local: bool = False
    key_url: str | None = None

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ValueError(
                f"ProviderEntry '{self.name}': unknown protocol '{self.protocol}' "
                f"— must be one of {PROTOCOLS}"
            )
        # Coerce list → tuple so the dataclass stays frozen/hashable
        object.__setattr__(self, "models", tuple(self.models))


class ProviderCatalog:
    """Loads and exposes the merged provider catalog."""

    @classmethod
    def load(cls) -> list[ProviderEntry]:
        """Return merged provider list: bundled entries + user overrides.

        Sort order: non-local entries grouped by protocol (anthropic → gemini →
        grok → openai, alphabetical within each group), then local providers
        (ollama, lmstudio), then the ``custom`` catch-all entry last.
        """
        # 1. ENTRY
        log.setup.debug("[provider_catalog] ProviderCatalog.load: entry")

        # 2. STEP — load bundled entries
        bundled = cls._load_dir(_BUNDLED_DIR, source="bundled")

        # 3. STEP — load user overrides from ~/.stackowl/providers/
        user_entries: list[ProviderEntry] = []
        try:
            from stackowl.paths import StackowlHome
            user_dir = StackowlHome.providers_dir()
            if user_dir.exists():
                user_entries = cls._load_dir(user_dir, source="user")
        except Exception as exc:
            log.setup.warning(
                "[provider_catalog] ProviderCatalog.load: could not load user overrides — %s", exc
            )

        # 4. DECISION — merge: user wins on name collision
        merged: dict[str, ProviderEntry] = {e.name: e for e in bundled}
        for entry in user_entries:
            if entry.name in merged:
                log.setup.debug(
                    "[provider_catalog] ProviderCatalog.load: user override for '%s'", entry.name
                )
            merged[entry.name] = entry

        result = cls._sort(list(merged.values()))

        # 5. EXIT
        log.setup.debug(
            "[provider_catalog] ProviderCatalog.load: exit",
            extra={"_fields": {"count": len(result)}},
        )
        return result

    # -- internal helpers -------------------------------------------------------

    @classmethod
    def _load_dir(cls, directory: Path, source: str) -> list[ProviderEntry]:
        entries: list[ProviderEntry] = []
        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                raw: dict[str, Any] = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                entry = ProviderEntry(**raw)
                entries.append(entry)
                log.setup.debug(
                    "[provider_catalog] _load_dir: loaded '%s' from %s", entry.name, source
                )
            except Exception as exc:
                log.setup.warning(
                    "[provider_catalog] _load_dir: skipping %s — %s: %s",
                    yaml_file.name,
                    type(exc).__name__,
                    exc,
                )
        return entries

    @staticmethod
    def _sort(entries: list[ProviderEntry]) -> list[ProviderEntry]:
        """Sort: custom last, locals second-to-last, then by protocol order, then name."""
        def _key(e: ProviderEntry) -> tuple[int, int, int, str]:
            is_custom = 1 if e.name == "custom" else 0
            is_local = 1 if e.is_local else 0
            proto_order = _PROTOCOL_ORDER.get(e.protocol, 99)
            return (is_custom, is_local, proto_order, e.label)

        return sorted(entries, key=_key)

    @staticmethod
    def protocol_label(protocol: str) -> str:
        return _PROTOCOL_LABEL.get(protocol, protocol.capitalize())
