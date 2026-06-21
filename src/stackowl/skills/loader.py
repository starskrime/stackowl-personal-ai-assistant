"""Unified SkillLoader — scans all four skill source dirs and registers them.

Subsumes the orphaned ``stackowl.plugins.skill_pack_loader.SkillPackLoader``
(deleted in Commit 3a per the placement vote). Adopts the common single-file
``SKILL.md`` format (Anthropic / Claude Code skills; frontmatter + markdown body)
so StackOwl skills are portable across the wider ecosystem.

Skill directory layout (each source dir contains many of these):

    ~/.stackowl/workspace/skills/<source>/<name>/
    ├── SKILL.md            ← REQUIRED frontmatter + markdown body
    ├── references/         ← optional — agent reads on demand (not auto-loaded)
    ├── scripts/            ← optional — agent invokes on demand (not auto-loaded)
    ├── assets/             ← optional — templates / data (not auto-loaded)
    ├── tools/*.py          ← OPTIONAL StackOwl extension — auto-registers Tool subclasses
    └── owls.yaml           ← OPTIONAL StackOwl extension — auto-registers OwlAgentManifests
                            (also accepts owls/manifest.yaml)

Each top-level call:
    loader = SkillLoader(tool_registry=..., owl_registry=...)
    loaded = await loader.load_all(skills_root, store=skill_index_store)
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from stackowl.exceptions import ToolRegistrationError
from stackowl.infra.observability import log
from stackowl.skills.manifest import SkillManifest, SkillSource
from stackowl.skills.skill_md import SkillMarkdownError, parse_skill_md
from stackowl.tools.base import Tool

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.skills.store import SkillIndexStore
    from stackowl.tools.registry import ToolRegistry


_VALID_SOURCES: tuple[SkillSource, ...] = ("builtin", "installed", "user", "learned")
_OWL_TRUSTED_SOURCES: frozenset[str] = frozenset({"builtin", "user"})
_SKILL_MD_FILENAME = "SKILL.md"
# A skill lives at <source>/<name>/ (flat) OR <source>/<category>/<name>/ (nested).
# Scan both layouts; never deeper, so a skill's own subdirs (references/, tools/,
# assets/) can't masquerade as nested skills.
_MAX_SKILL_DEPTH = 2


def _discover_skill_dirs(source_dir: Path, *, max_depth: int = _MAX_SKILL_DEPTH) -> list[Path]:
    """Return every dir holding a ``SKILL.md`` at depth 1..``max_depth`` under ``source_dir``.

    Leaf-stopping: once a directory contains ``SKILL.md`` it IS a skill and we do
    not descend into it (so ``references/SKILL.md`` etc. are never picked up).
    Dirs starting with ``_`` are reserved (``_deprecated``, ``__pycache__``) and
    are neither loaded nor descended.
    """
    found: list[Path] = []

    def _walk(directory: Path, depth: int) -> None:
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if (child / _SKILL_MD_FILENAME).exists():
                found.append(child)  # leaf skill — do not descend
                continue
            if depth < max_depth:
                _walk(child, depth + 1)

    _walk(source_dir, 1)
    return found


def _category_for(source_dir: Path, skill_dir: Path) -> str | None:
    """Derive a skill's category from its path: ``<category>`` in
    ``<source>/<category>/<name>/``; ``None`` for a flat ``<source>/<name>/``."""
    rel = skill_dir.relative_to(source_dir)
    return rel.parts[-2] if len(rel.parts) >= 2 else None


@dataclass(frozen=True)
class LoadedSkill:
    """Lightweight record returned from a successful skill load."""

    manifest: SkillManifest
    path: Path
    body: str
    tools_registered: int
    owls_registered: int
    tool_names: tuple[str, ...] = ()


class SkillLoadError(Exception):
    """Raised when a skill directory's SKILL.md is invalid or unreadable."""


class SkillLoader:
    """Scans every source dir under ``skills_root`` and registers each skill."""

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        owl_registry: OwlRegistry | None = None,
    ) -> None:
        # 1. ENTRY
        log.skills.debug(
            "[skills] loader.init: ready",
            extra={"_fields": {
                "has_tool_registry": tool_registry is not None,
                "has_owl_registry": owl_registry is not None,
            }},
        )
        self._tool_registry = tool_registry
        self._owl_registry = owl_registry

    async def load_all(
        self,
        skills_root: Path,
        *,
        store: SkillIndexStore | None = None,
        builtin_seed_dir: Path | None = None,
    ) -> list[LoadedSkill]:
        """Scan every source dir under ``skills_root`` and register each skill.

        ``builtin_seed_dir`` (optional) — if given, contents are idempotently
        copied to ``skills_root/builtin/`` before scanning. This is how shipped
        skills land on disk on every gateway boot.

        ``store`` (optional) — if given, every loaded SKILL.md is upserted
        into the SQLite skills index (manifest fields + body for FTS).
        """
        # 1. ENTRY
        log.skills.info(
            "[skills] loader.load_all: entry",
            extra={"_fields": {
                "skills_root": str(skills_root),
                "has_builtin_seed": builtin_seed_dir is not None,
                "has_store": store is not None,
            }},
        )
        skills_root.mkdir(parents=True, exist_ok=True)
        # 2. DECISION — idempotent builtin seeding
        if builtin_seed_dir is not None:
            self._seed_builtins(builtin_seed_dir, skills_root / "builtin")

        # 3. STEP — scan every source dir (flat <name>/ AND nested <category>/<name>/)
        loaded: list[LoadedSkill] = []
        for source in _VALID_SOURCES:
            source_dir = skills_root / source
            if not source_dir.is_dir():
                continue
            for skill_dir in _discover_skill_dirs(source_dir):
                category = _category_for(source_dir, skill_dir)
                try:
                    result = self._load_one(skill_dir, source, category=category)
                except SkillLoadError as exc:
                    # B5 — never silent
                    log.skills.warning(
                        "[skills] loader.load_all: skill invalid — skipping",
                        exc_info=exc,
                        extra={"_fields": {"path": str(skill_dir)}},
                    )
                    continue
                except ToolRegistrationError:
                    # PLUG-3/F047 — a genuine cross-source tool-name collision is a
                    # hard, actionable error, NOT a "skip this skill" case. Propagate
                    # so the caller (reindex) can surface a distinct collision
                    # message instead of a misleading generic "reindex pending".
                    raise
                except Exception as exc:  # B5
                    log.skills.error(
                        "[skills] loader.load_all: unexpected error — skipping",
                        exc_info=exc,
                        extra={"_fields": {"path": str(skill_dir)}},
                    )
                    continue
                loaded.append(result)
                if store is not None:
                    try:
                        await store.upsert(result)
                    except Exception as exc:  # B5
                        log.skills.warning(
                            "[skills] loader.load_all: store.upsert failed — skipping",
                            exc_info=exc,
                            extra={"_fields": {"name": result.manifest.name}},
                        )

        # 4. EXIT
        log.skills.info(
            "[skills] loader.load_all: exit",
            extra={"_fields": {
                "loaded_count": len(loaded),
                "by_source": _count_by_source(loaded),
            }},
        )
        return loaded

    # ----- internals --------------------------------------------------------

    def _load_one(
        self, skill_dir: Path, source: SkillSource, *, category: str | None = None,
    ) -> LoadedSkill:
        """Load one skill directory. Raises SkillLoadError on validation failure."""
        # 1. ENTRY
        log.skills.debug(
            "[skills] loader._load_one: entry",
            extra={"_fields": {"path": str(skill_dir), "source": source}},
        )
        skill_md_path = skill_dir / _SKILL_MD_FILENAME
        if not skill_md_path.exists():
            raise SkillLoadError(f"missing {_SKILL_MD_FILENAME} at {skill_md_path}")

        # 3. STEP — parse frontmatter + body
        try:
            text = skill_md_path.read_text(encoding="utf-8")
            parsed = parse_skill_md(text)
        except SkillMarkdownError as exc:
            raise SkillLoadError(str(exc)) from exc
        except OSError as exc:
            raise SkillLoadError(f"cannot read {skill_md_path}: {exc}") from exc

        # 2. DECISION — force source = dir so frontmatter can't lie; derive
        # category from the directory layout (<source>/<category>/<name>/) when
        # nested. Flat skills keep any frontmatter-declared category.
        fm: dict[str, object] = dict(parsed.frontmatter)
        fm["source"] = source
        if category is not None:
            fm["category"] = category
        try:
            manifest = SkillManifest.model_validate(fm)
        except Exception as exc:
            raise SkillLoadError(
                f"SKILL.md frontmatter validation failed at {skill_md_path}: {exc}",
            ) from exc

        # 3. STEP — optional StackOwl extension sidecars
        tool_names: tuple[str, ...] = ()
        tools_dir = skill_dir / "tools"
        if tools_dir.exists() and self._tool_registry is not None:
            tool_names = self._load_tools(tools_dir, manifest.name)

        owls_count = 0
        owls_manifest = _resolve_owls_manifest(skill_dir)
        if owls_manifest is not None and self._owl_registry is not None:
            if source in _OWL_TRUSTED_SOURCES:
                owls_count = self._load_owls(owls_manifest, manifest.name)
            else:
                log.skills.warning(
                    "[skills] loader: refusing owls.yaml from untrusted source",
                    extra={"_fields": {"source": source, "skill": manifest.name}},
                )

        # 4. EXIT
        log.skills.info(
            "[skills] loader._load_one: registered",
            extra={"_fields": {
                "name": manifest.name, "source": source,
                "tools": len(tool_names), "owls": owls_count,
                "body_len": len(parsed.body),
            }},
        )
        return LoadedSkill(
            manifest=manifest, path=skill_dir, body=parsed.body,
            tools_registered=len(tool_names), owls_registered=owls_count,
            tool_names=tool_names,
        )

    def _load_tools(self, tools_dir: Path, source_name: str) -> tuple[str, ...]:
        """Import every ``.py`` under tools_dir, register Tool subclasses found.

        Returns a tuple of successfully registered tool names (one per tool).
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] loader._load_tools: entry",
            extra={"_fields": {"dir": str(tools_dir), "source": source_name}},
        )
        if self._tool_registry is None:
            return ()
        names: list[str] = []
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            if spec is None or spec.loader is None:
                log.skills.warning(
                    "[skills] loader._load_tools: spec missing — skipping",
                    extra={"_fields": {"file": str(py_file)}},
                )
                continue
            try:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:  # B5
                log.skills.error(
                    "[skills] loader._load_tools: module load failed",
                    exc_info=exc,
                    extra={"_fields": {"file": str(py_file)}},
                )
                continue
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if isinstance(obj, type) and issubclass(obj, Tool) and obj is not Tool:
                    try:
                        instance = obj()
                    except Exception as exc:  # B5 — a tool that can't instantiate
                        log.skills.error(
                            "[skills] loader._load_tools: tool instantiation failed",
                            exc_info=exc,
                            extra={"_fields": {"tool": attr_name, "source": source_name}},
                        )
                        continue
                    # PLUG-3/F047 — distinguish an idempotent re-register of the
                    # SAME source (re-author / reindex over the whole tree) from a
                    # genuine cross-source name collision.
                    owner = self._tool_registry.source_of(instance.name)
                    if owner is not None and owner != source_name:
                        # Genuine collision: a DIFFERENT skill already owns the
                        # name. Surface a distinct, owner-naming error (NOT a silent
                        # skip and NOT a misleading "reindex pending") so the
                        # re-author is told exactly what conflicts.
                        raise ToolRegistrationError(
                            instance.name,
                            f"name collision — already registered by skill "
                            f"{owner!r}; rename the tool or remove the other skill",
                        )
                    # Same-source (or first-time) registration: replace=True makes a
                    # re-author of a learned/user skill idempotent. The registry
                    # STILL refuses to replace a dangerous-category tool even with
                    # replace=True, so the consent boundary is intact.
                    try:
                        self._tool_registry.register(
                            instance, source_name=source_name, replace=True,
                        )
                        names.append(instance.name)
                    except Exception as exc:  # B5 — dangerous-shadow refusal etc.
                        log.skills.error(
                            "[skills] loader._load_tools: tool registration failed",
                            exc_info=exc,
                            extra={"_fields": {"tool": attr_name, "source": source_name}},
                        )
        # 4. EXIT
        log.skills.debug(
            "[skills] loader._load_tools: exit",
            extra={"_fields": {"source": source_name, "registered": len(names)}},
        )
        return tuple(names)

    def _load_owls(self, owls_yaml: Path, source_name: str) -> int:
        """Parse owls.yaml + register each OwlAgentManifest with the registry."""
        # 1. ENTRY
        log.skills.debug(
            "[skills] loader._load_owls: entry",
            extra={"_fields": {"file": str(owls_yaml), "source": source_name}},
        )
        if self._owl_registry is None:
            return 0
        try:
            raw_list = yaml.safe_load(owls_yaml.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            log.skills.warning(
                "[skills] loader._load_owls: yaml parse failed — skipping",
                exc_info=exc,
                extra={"_fields": {"file": str(owls_yaml)}},
            )
            return 0
        if not isinstance(raw_list, list):
            log.skills.warning(
                "[skills] loader._load_owls: owls.yaml must be a list — skipping",
                extra={"_fields": {"file": str(owls_yaml)}},
            )
            return 0
        from stackowl.owls.manifest import OwlAgentManifest

        count = 0
        for item in raw_list:
            try:
                manifest = OwlAgentManifest(**item)
                self._owl_registry.register(manifest, source_name=source_name)
                count += 1
            except Exception as exc:  # B5
                log.skills.error(
                    "[skills] loader._load_owls: owl registration failed — skipping one",
                    exc_info=exc,
                    extra={"_fields": {"source": source_name}},
                )
        # 4. EXIT
        log.skills.debug(
            "[skills] loader._load_owls: exit",
            extra={"_fields": {"source": source_name, "registered": count}},
        )
        return count

    def _seed_builtins(self, seed_src: Path, seed_dst: Path) -> None:
        """Idempotent copy of shipped builtin skills into workspace/skills/builtin/.

        Runs on every boot. If a skill exists at the destination already, we
        REPLACE it from the package source so upgrades propagate. (Builtin
        is intentionally not user-editable — see the security boundary.)
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] loader._seed_builtins: entry",
            extra={"_fields": {"src": str(seed_src), "dst": str(seed_dst)}},
        )
        if not seed_src.is_dir():
            log.skills.debug(
                "[skills] loader._seed_builtins: exit — no source dir",
                extra={"_fields": {"src": str(seed_src)}},
            )
            return
        seed_dst.mkdir(parents=True, exist_ok=True)
        copied = 0
        for child in seed_src.iterdir():
            if not child.is_dir() or child.name.startswith("_") or child.name == "__pycache__":
                continue
            target = seed_dst / child.name
            try:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target)
                copied += 1
            except Exception as exc:  # B5
                log.skills.warning(
                    "[skills] loader._seed_builtins: copy failed — skipping",
                    exc_info=exc,
                    extra={"_fields": {"child": child.name}},
                )
        # 4. EXIT
        log.skills.info(
            "[skills] loader._seed_builtins: exit",
            extra={"_fields": {"copied": copied}},
        )


def _resolve_owls_manifest(skill_dir: Path) -> Path | None:
    """Find the optional owl manifest — supports both flat and dir layouts."""
    flat = skill_dir / "owls.yaml"
    if flat.exists():
        return flat
    nested = skill_dir / "owls" / "manifest.yaml"
    if nested.exists():
        return nested
    return None


def _count_by_source(loaded: list[LoadedSkill]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in loaded:
        counts[s.manifest.source] = counts.get(s.manifest.source, 0) + 1
    return counts
