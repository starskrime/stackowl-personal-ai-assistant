"""Pure helpers for :mod:`stackowl.commands.owls_command`.

Kept separate so the command class itself stays under the 300-line B2 budget
and helpers can be unit-tested in isolation.  No I/O — all functions are
total and side-effect-free.
"""

from __future__ import annotations

import shlex
from typing import Any, get_args

from stackowl.exceptions import CommandParseError
from stackowl.infra.observability import log
from stackowl.owls.builder import OwlSpec, SpecialistOwlBuilder
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import ModelTier, OwlAgentManifest

# Derived from the manifest's ModelTier Literal so the CLI allowlist can never
# drift from the field's accepted values (single source of truth).
_VALID_TIERS: frozenset[str] = frozenset(get_args(ModelTier))

_DNA_TRAITS: tuple[str, ...] = (
    "challenge_level",
    "verbosity",
    "curiosity",
    "formality",
    "creativity",
    "precision",
)

# A trait that deviates less than this from 0.5 is considered "neutral".
_NEUTRAL_EPSILON = 0.05

# Trait abbreviation used in the table summary column (no English keywords —
# letters are pure trait initials, not stopwords).
_TRAIT_ABBR: dict[str, str] = {
    "challenge_level": "ch",
    "verbosity": "vb",
    "curiosity": "cu",
    "formality": "fm",
    "creativity": "cr",
    "precision": "pr",
}


def _dna_summary(dna: OwlDNA) -> str:
    """Return ``"neutral"`` if every trait is near 0.5, else dominant abbreviation."""
    dominant_trait: str | None = None
    dominant_dev: float = 0.0
    for trait in _DNA_TRAITS:
        value = float(getattr(dna, trait))
        deviation = abs(value - 0.5)
        if deviation < _NEUTRAL_EPSILON:
            continue
        if deviation > dominant_dev:
            dominant_dev = deviation
            dominant_trait = trait
    if dominant_trait is None:
        return "neutral"
    direction = "+" if float(getattr(dna, dominant_trait)) > 0.5 else "-"
    return f"{_TRAIT_ABBR[dominant_trait]}{direction}"


def _pad(value: str, width: int) -> str:
    """Right-pad ``value`` to ``width`` chars (ASCII spaces); truncates if too long."""
    if len(value) > width:
        return value[: max(0, width - 1)] + "…"
    return value + " " * (width - len(value))


def format_owl_table(manifests: list[OwlAgentManifest]) -> str:
    """Render a fixed-width ASCII table summarising registered owls.

    Columns: Name | Role | Tier | Provider | DNA | Concurrent.
    Uses only ASCII ``|`` and ``-`` — no unicode box-drawing characters.
    """
    log.gateway.debug(
        "[commands] owls.format_owl_table: entry",
        extra={"_fields": {"count": len(manifests)}},
    )
    if not manifests:
        return "(no owls registered)"
    widths: dict[str, int] = {"name": 16, "role": 20, "tier": 10, "provider": 14, "dna": 8, "conc": 10}
    header_row = (
        f"{_pad('Name', widths['name'])} | "
        f"{_pad('Role', widths['role'])} | "
        f"{_pad('Tier', widths['tier'])} | "
        f"{_pad('Provider', widths['provider'])} | "
        f"{_pad('DNA', widths['dna'])} | "
        f"{_pad('Concurrent', widths['conc'])}"
    )
    separator = "-" * len(header_row)
    lines: list[str] = [header_row, separator]
    for manifest in manifests:
        provider = manifest.provider_name or "(auto)"
        lines.append(
            f"{_pad(manifest.name, widths['name'])} | "
            f"{_pad(manifest.role, widths['role'])} | "
            f"{_pad(manifest.model_tier, widths['tier'])} | "
            f"{_pad(provider, widths['provider'])} | "
            f"{_pad(_dna_summary(manifest.dna), widths['dna'])} | "
            f"{_pad(str(manifest.max_concurrent_requests), widths['conc'])}"
        )
    log.gateway.debug(
        "[commands] owls.format_owl_table: exit",
        extra={"_fields": {"rows": len(manifests)}},
    )
    return "\n".join(lines)


def _bar(value: float, width: int = 10) -> str:
    """Return a fixed-width unicode block bar representing ``value`` in [0,1]."""
    clamped = max(0.0, min(1.0, value))
    filled = round(clamped * width)
    return "▓" * filled + "░" * (width - filled)


def format_dna_display(
    owl_name: str,
    dna: OwlDNA,
    db_row: dict[str, Any] | None,
    authored: OwlDNA | None = None,
) -> str:
    """Render DNA traits as a vertical table with bar visualisation.

    ``db_row`` is the row fetched from ``owl_dna`` (or ``None`` if the owl has
    never been persisted — uses manifest-level defaults).  The footer reports
    the timestamp of the last persisted mutation or a "not yet persisted" hint.

    When ``authored`` is provided, a second column shows the authored baseline
    value alongside the current value so divergence is immediately visible.
    Existing callers that omit ``authored`` are unaffected.
    """
    log.gateway.debug(
        "[commands] owls.format_dna_display: entry",
        extra={"_fields": {"owl": owl_name, "has_db_row": db_row is not None, "has_authored": authored is not None}},
    )
    lines: list[str] = [f"DNA for owl: {owl_name}", "-" * 48]
    if authored is not None:
        lines.append("  Trait             Current  Authored")
        lines.append("  " + "-" * 38)
        for trait in _DNA_TRAITS:
            current_val = float(db_row[trait]) if db_row is not None and trait in db_row else float(getattr(dna, trait))
            current_val = max(0.0, min(1.0, current_val))
            authored_val = max(0.0, min(1.0, float(getattr(authored, trait))))
            marker = " *" if abs(current_val - authored_val) > 0.01 else "  "
            lines.append(
                f"  {_pad(trait, 16)}: {current_val:.2f} [{_bar(current_val)}]"
                f"  authored={authored_val:.2f}{marker}"
            )
        lines.append("")
        lines.append("  (* trait has drifted from authored baseline)")
    else:
        for trait in _DNA_TRAITS:
            value = float(db_row[trait]) if db_row is not None and trait in db_row else float(getattr(dna, trait))
            clamped = max(0.0, min(1.0, value))
            lines.append(f"  {_pad(trait, 16)}: {clamped:.2f} [{_bar(clamped)}]")
    if db_row is not None and db_row.get("updated_at"):
        lines.append("")
        lines.append(f"  updated_at: {db_row['updated_at']}")
    else:
        lines.append("")
        lines.append("  updated_at: not yet persisted")
    log.gateway.debug(
        "[commands] owls.format_dna_display: exit",
        extra={"_fields": {"owl": owl_name, "lines": len(lines)}},
    )
    return "\n".join(lines)


def parse_add_args(rest: str) -> dict[str, Any]:
    """Tokenise the ``/owls add`` payload via :func:`shlex.split`.

    Required: ``<name> --role <r> --tier <t>``.
    Optional flags: ``--provider <p>``, ``--temperature <f>``, ``--tools <a,b>``.
    Raises :class:`CommandParseError` on any malformed input.
    """
    log.gateway.debug(
        "[commands] owls.parse_add_args: entry",
        extra={"_fields": {"rest_len": len(rest)}},
    )
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        log.gateway.warning(
            "[commands] owls.parse_add_args: shlex failed",
            extra={"_fields": {"error": str(exc)}},
        )
        raise CommandParseError("owls add", f"could not tokenise arguments: {exc}") from exc
    if not tokens:
        raise CommandParseError("owls add", "missing owl name")
    name = tokens[0]
    flags = tokens[1:]
    if len(flags) % 2 != 0:
        raise CommandParseError("owls add", "every --flag requires a value")
    params: dict[str, Any] = {
        "name": name,
        "role": None,
        "tier": None,
        "provider": None,
        "temperature": None,
        "tools": [],
        "preset": None,
        "skills": [],
        "capability_profile": [],
        "system_prompt": None,
    }
    i = 0
    while i < len(flags):
        key, value = flags[i], flags[i + 1]
        if key == "--role":
            params["role"] = value
        elif key == "--tier":
            params["tier"] = value
        elif key == "--provider":
            params["provider"] = value
        elif key == "--temperature":
            try:
                params["temperature"] = float(value)
            except ValueError as exc:
                raise CommandParseError("owls add", f"--temperature must be float, got {value!r}") from exc
        elif key == "--tools":
            params["tools"] = [tool.strip() for tool in value.split(",") if tool.strip()]
        elif key == "--preset":
            params["preset"] = value
        elif key == "--skills":
            params["skills"] = [s.strip() for s in value.split(",") if s.strip()]
        elif key == "--capability-profile":
            params["capability_profile"] = [s.strip() for s in value.split(",") if s.strip()]
        elif key == "--system-prompt":
            params["system_prompt"] = value
        else:
            raise CommandParseError("owls add", f"unknown flag: {key}")
        i += 2
    if not params["role"]:
        raise CommandParseError("owls add", "missing required flag --role")
    if not params["tier"]:
        raise CommandParseError("owls add", "missing required flag --tier")
    if params["tier"] not in _VALID_TIERS:
        raise CommandParseError(
            "owls add",
            f"--tier must be one of {sorted(_VALID_TIERS)}, got {params['tier']!r}",
        )
    log.gateway.debug(
        "[commands] owls.parse_add_args: exit",
        extra={"_fields": {"name": params["name"], "tier": params["tier"]}},
    )
    return params


def parse_edit_args(rest: str) -> dict[str, Any]:
    """Parse ``/owls edit <name> [--flag value ...]`` — every flag optional.

    Raises :class:`CommandParseError` on malformed input or unknown flags.
    """
    log.gateway.debug(
        "[commands] owls.parse_edit_args: entry",
        extra={"_fields": {"rest_len": len(rest)}},
    )
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise CommandParseError("owls edit", f"could not tokenise arguments: {exc}") from exc
    if not tokens:
        raise CommandParseError("owls edit", "missing owl name")
    name, flags = tokens[0], tokens[1:]
    if len(flags) % 2 != 0:
        raise CommandParseError("owls edit", "every --flag requires a value")
    changes: dict[str, Any] = {"name": name}
    mapping = {
        "--role": "role",
        "--tier": "model_tier",
        "--provider": "provider_name",
        "--system-prompt": "system_prompt",
    }
    i = 0
    while i < len(flags):
        key, value = flags[i], flags[i + 1]
        if key in mapping:
            changes[mapping[key]] = value
        elif key == "--temperature":
            try:
                changes["temperature"] = float(value)
            except ValueError as exc:
                raise CommandParseError("owls edit", f"--temperature must be float, got {value!r}") from exc
        elif key == "--skills":
            changes["skills"] = tuple(s.strip() for s in value.split(",") if s.strip())
        elif key == "--capability-profile":
            changes["capability_profile"] = [s.strip() for s in value.split(",") if s.strip()]
        else:
            raise CommandParseError("owls edit", f"unknown flag: {key}")
        i += 2
    if changes.get("model_tier") and changes["model_tier"] not in _VALID_TIERS:
        raise CommandParseError("owls edit", f"--tier must be one of {sorted(_VALID_TIERS)}")
    log.gateway.debug(
        "[commands] owls.parse_edit_args: exit",
        extra={"_fields": {"name": name, "fields_changed": list(changes.keys())}},
    )
    return changes


def build_owl_manifest(
    params: dict[str, Any], *, valid_tools: frozenset[str] | None = None
) -> OwlAgentManifest:
    """Adapter: parsed CLI params -> OwlSpec -> the single SpecialistOwlBuilder.

    No manifest is constructed here — the builder is the one constructor (DRY)."""
    log.gateway.debug(
        "[commands] owls.build_owl_manifest: entry",
        extra={"_fields": {"name": params.get("name"), "preset": params.get("preset")}},
    )
    temperature_raw = params.get("temperature")
    spec = OwlSpec(
        name=params["name"],
        role=params["role"],
        model_tier=params["tier"],
        preset=params.get("preset"),
        explicit_tools=tuple(params.get("tools") or ()),
        skills=tuple(params.get("skills") or ()),
        capability_profile=tuple(params.get("capability_profile") or ()),
        provider_name=params.get("provider"),
        temperature=float(temperature_raw) if temperature_raw is not None else 0.7,
        system_prompt=params.get("system_prompt"),
        valid_tools=valid_tools,
    )
    try:
        manifest = SpecialistOwlBuilder().build(spec)
    except ValueError as exc:
        raise CommandParseError("owls add", str(exc)) from exc
    log.gateway.debug(
        "[commands] owls.build_owl_manifest: exit",
        extra={"_fields": {"name": manifest.name}},
    )
    return manifest


def manifest_to_yaml_entry(manifest: OwlAgentManifest) -> dict[str, Any]:
    """Serialise a manifest into a minimal YAML-friendly dict for persistence."""
    entry: dict[str, Any] = {
        "name": manifest.name,
        "role": manifest.role,
        "system_prompt": manifest.system_prompt,
        "model_tier": manifest.model_tier,
        "temperature": manifest.temperature,
    }
    if manifest.provider_name:
        entry["provider_name"] = manifest.provider_name
    if manifest.tools:
        entry["tools"] = list(manifest.tools)
    if manifest.capability_profile:
        entry["capability_profile"] = list(manifest.capability_profile)
    if manifest.skills:
        entry["skills"] = list(manifest.skills)
    if manifest.bounds is not None:
        # model_dump(mode="json") turns frozenset/tuple into list — ruamel cannot
        # represent frozenset/tuple and would raise RepresenterError otherwise.
        bounds = manifest.bounds.model_dump(mode="json", exclude_none=True)
        # The tools axis is a frozenset → its dumped list order is non-deterministic
        # across processes; sort it so re-saving a manifest yields stable yaml diffs.
        if isinstance(bounds.get("tools"), list):
            bounds["tools"] = sorted(bounds["tools"])
        entry["bounds"] = bounds
    entry["origin"] = manifest.origin
    if manifest.created_by is not None:
        entry["created_by"] = manifest.created_by
    if manifest.creation_ceiling is not None:
        ceiling = manifest.creation_ceiling.model_dump(mode="json", exclude_none=True)
        if isinstance(ceiling.get("tools"), list):
            ceiling["tools"] = sorted(ceiling["tools"])
        entry["creation_ceiling"] = ceiling
    return entry
