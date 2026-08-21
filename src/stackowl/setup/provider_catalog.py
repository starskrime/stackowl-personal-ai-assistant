"""ProviderCatalog — extensible registry of concrete AI providers.

Each provider declares which of the four base protocols it speaks
(anthropic | openai | gemini | grok) so the rest of the system never
needs to branch on provider names.

Bundled definitions live in src/stackowl/setup/providers/*.yaml.
User providers live in ~/.stackowl/providers/*.yaml. A file may ADD a
provider the bundle does not carry; it may NOT replace a bundled entry.
A name collision is refused and logged at INFO (ESC-23, 2026-08-21) —
``ProviderEntry`` carries ``base_url``, and the add-token flow sends
that URL the operator's raw credential to validate it, so allowing a
replacement allowed a local file to redirect the next token typed.
"""

from __future__ import annotations

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
    # Subset of ``models`` known to be vision/multimodal-capable (E10-S1). Lets the
    # onboarding picker surface a vision-capable choice; the runtime capability flag
    # is now `ProviderConfig.supports_vision`, which DEFAULTS TO ENABLED (Bakir's
    # standing rule, 2026-08-20). This catalog field stays a PICKER hint for
    # onboarding only — it never decided the runtime flag, and now there is no
    # runtime list for it to disagree with.
    vision_models: tuple[str, ...] = field(default_factory=tuple)
    tier: str = "powerful"
    needs_api_key: bool = True
    is_local: bool = False
    key_url: str | None = None
    # NEW — optional tags for browse/search filtering (add/tier UX). Empty
    # default means every existing bundled YAML file parses unchanged.
    category: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ValueError(
                f"ProviderEntry '{self.name}': unknown protocol '{self.protocol}' "
                f"— must be one of {PROTOCOLS}"
            )
        # Coerce list → tuple so the dataclass stays frozen/hashable
        object.__setattr__(self, "models", tuple(self.models))
        object.__setattr__(self, "vision_models", tuple(self.vision_models))
        object.__setattr__(self, "category", tuple(self.category))


class ProviderCatalog:
    """Loads and exposes the merged provider catalog."""

    @classmethod
    def load(cls) -> list[ProviderEntry]:
        """Return the merged provider list: bundled entries plus user ADDITIONS.

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

        # 4. DECISION — merge: ADDITIVE only, bundled wins on a name collision
        merged: dict[str, ProviderEntry] = {e.name: e for e in bundled}
        for entry in user_entries:
            if entry.name in merged:
                # ADDITIVE-ONLY since 2026-08-21 (ESC-23, Bakir's call). A user file
                # may INTRODUCE a provider; it may not REPLACE a bundled one.
                #
                # This deliberately removes an advertised capability — the module
                # docstring described the override and setup/minimal.py invites it —
                # so it was escalated rather than assumed. The reason it goes:
                # `ProviderEntry` carries `base_url`, and
                # `commands/provider_command.py` `_add_discover` sends that base_url
                # the operator's RAW token in order to validate it, BEFORE
                # `store_secret` runs. A file redefining a bundled entry therefore
                # redirects the next credential typed for that name. That is the same
                # risk class that got last-writer-wins rejected for plugin profiles;
                # it was simply already shipped, one layer down.
                #
                # REFUSED AT INFO, never in silence. Until 2026-08-21 the replacement
                # was announced at DEBUG, which production never records.
                previous = merged[entry.name]
                log.setup.info(
                    "[provider_catalog] REFUSED a user file that would replace a "
                    "bundled provider — a user provider may add a new name, never "
                    "redefine a built-in one; the bundled entry is kept",
                    extra={"_fields": {
                        "name": entry.name,
                        "kept_base_url": previous.base_url or "",
                        "ignored_base_url": entry.base_url or "",
                        "would_have_changed_base_url": (
                            entry.base_url != previous.base_url
                        ),
                    }},
                )
                continue
            merged[entry.name] = entry

        result = cls._sort(list(merged.values()))

        # 5. EXIT
        log.setup.debug(
            "[provider_catalog] ProviderCatalog.load: exit",
            extra={"_fields": {"count": len(result)}},
        )
        return result

    @classmethod
    def search(cls, query: str) -> list[ProviderEntry]:
        """Case-insensitive substring match against name/label/category."""
        # 1. ENTRY
        log.setup.debug(
            "[provider_catalog] ProviderCatalog.search: entry",
            extra={"_fields": {"query_len": len(query)}},
        )

        # 2. DECISION
        needle = query.strip().casefold()
        if not needle:
            # 3. STEP — empty query returns all
            result = cls.load()
            log.setup.debug(
                "[provider_catalog] ProviderCatalog.search: empty query, returning all",
                extra={"_fields": {"matches": len(result)}},
            )
            return result

        # 3. STEP — perform substring match
        result = [
            e for e in cls.load()
            if needle in e.name.casefold()
            or needle in e.label.casefold()
            or any(needle in c.casefold() for c in e.category)
        ]

        # 4. EXIT
        log.setup.debug(
            "[provider_catalog] ProviderCatalog.search: exit",
            extra={"_fields": {"matches": len(result)}},
        )
        return result

    @classmethod
    def browse(cls, category: str | None = None) -> list[ProviderEntry]:
        """Return the catalog, optionally filtered to one category tag."""
        # 1. ENTRY
        log.setup.debug(
            "[provider_catalog] ProviderCatalog.browse: entry",
            extra={"_fields": {"category": category}},
        )

        # 2. DECISION
        entries = cls.load()
        if category is None:
            # 3. STEP — no filter, return all
            log.setup.debug(
                "[provider_catalog] ProviderCatalog.browse: no filter, returning all",
                extra={"_fields": {"matches": len(entries)}},
            )
            return entries

        # 3. STEP — filter by exact category match (case-insensitive)
        needle = category.casefold()
        result = [e for e in entries if any(c.casefold() == needle for c in e.category)]

        # 4. EXIT
        log.setup.debug(
            "[provider_catalog] ProviderCatalog.browse: exit",
            extra={"_fields": {"matches": len(result)}},
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
