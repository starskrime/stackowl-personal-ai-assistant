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
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES
from stackowl.owls.manifest import ModelTier, OwlAgentManifest
from stackowl.owls.shadow_validator import ShadowValidationResult

# Derived from the manifest's ModelTier Literal so the CLI allowlist can never
# drift from the field's accepted values (single source of truth).
_VALID_TIERS: frozenset[str] = frozenset(get_args(ModelTier))

_DNA_TRAITS: tuple[str, ...] = TRAIT_NAMES

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
    "completion_drive": "cd",
}


def _dna_summary(dna: OwlDNA) -> str:
    """Return ``"neutral"`` if every trait is near 0.5, else dominant abbreviation."""
    dominant_trait: str | None = None
    dominant_dev: float = 0.0
    for trait in _DNA_TRAITS:
        value = float(getattr(dna, trait))
        deviation = abs(value - NEUTRAL)
        if deviation < _NEUTRAL_EPSILON:
            continue
        if deviation > dominant_dev:
            dominant_dev = deviation
            dominant_trait = trait
    if dominant_trait is None:
        return "neutral"
    direction = "+" if float(getattr(dna, dominant_trait)) > NEUTRAL else "-"
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


def format_owl_roster(manifests: list[OwlAgentManifest]) -> str:
    """Render the friendly "your assistants" roster for non-technical users.

    One line per owl: spoken name — what it does · lifecycle status. A scheduled
    owl is 🟢 active (it works in the background); an on-demand owl is 💤 resting
    (it works when summoned). Shows the human ``display`` name, never the system
    slug/IDs/tier — the user's world is the name and what it does. (UniOwl S14)
    """
    log.gateway.debug(
        "[commands] owls.format_owl_roster: entry",
        extra={"_fields": {"count": len(manifests)}},
    )
    if not manifests:
        return "You have no assistants yet. Tell me what you'd like one to do."
    lines: list[str] = ["Your assistants 🦉"]
    for m in sorted(manifests, key=lambda x: x.display.casefold()):
        status = "🟢 active" if m.lifecycle == "scheduled" else "💤 resting"
        does = (m.role or "").strip() or "general help"
        lines.append(f"{m.display} — {does} · {status}")
    log.gateway.debug(
        "[commands] owls.format_owl_roster: exit",
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


def format_dry_run_report(
    owl_name: str,
    result: ShadowValidationResult,
    n_consecutive_required: int,
) -> str:
    """Render Story 2.7's manual dry-run report: pass/fail + gate detail.

    Mirrors :func:`format_dna_display`'s header/separator/indented-line
    convention rather than inventing a new style. On failure, includes each
    held-out replay's failure reason and (truncated) input text — AC #1's
    "the specific non-regression that failed," surfaced here too so an
    operator triggering the dry-run interactively sees the same detail the
    enriched rejection log carries.
    """
    status = "PASS" if result.passed else "FAIL"
    lines: list[str] = [f"Dry-run for owl: {owl_name} — {status}", "-" * 48]
    lines.append(f"  n_replayed: {result.n_replayed}")
    lines.append(f"  consecutive_non_regressions: {result.consecutive_non_regressions}")
    lines.append(f"  n_consecutive_required: {n_consecutive_required}")
    if not result.passed and result.failures:
        lines.append("")
        lines.append("  Failures:")
        for f in result.failures:
            reason = f.get("reason", "")
            input_text = str(f.get("input_text", ""))[:200]
            lines.append(f"    - reason={reason} input={input_text!r}")
    return "\n".join(lines)


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


# /owl create|edit flag grammar → owl_build.execute kwargs. Free text with no
# --flags is treated as a specialty sentence (the free-text create path).
_OWL_BUILD_FLAGS: dict[str, str] = {
    "--name": "name",
    "--preset": "preset",
    "--specialty": "specialty",
    "--schedule": "schedule",
    "--goal": "goal",
    "--lifecycle": "lifecycle",
    "--boundaries": "boundaries",
    "--evolution_strategy": "evolution_strategy",
    "--report": "report",
    "--tier": "model_tier",
    "--model_tier": "model_tier",
}


def parse_owl_build_flags(rest: str) -> dict[str, Any]:
    """Parse a `/owl create|edit` payload into owl_build.execute kwargs.

    ``--explicit_tools`` takes a comma list; every other flag takes one value.
    A payload with no ``--flags`` is a free-text specialty sentence → the
    free-text create path (elicits any missing fields). Raises CommandParseError
    on a malformed flag pairing or an unknown flag."""
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise CommandParseError("owl", f"could not tokenise arguments: {exc}") from exc
    if not tokens:
        return {}
    if not any(t.startswith("--") for t in tokens):
        return {"specialty": rest.strip()}
    if len(tokens) % 2 != 0:
        raise CommandParseError("owl", "every --flag requires a value")
    kwargs: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        key, value = tokens[i], tokens[i + 1]
        if key == "--explicit_tools":
            kwargs["explicit_tools"] = [t.strip() for t in value.split(",") if t.strip()]
        elif key in _OWL_BUILD_FLAGS:
            kwargs[_OWL_BUILD_FLAGS[key]] = value
        else:
            raise CommandParseError("owl", f"unknown flag: {key}")
        i += 2
    return kwargs


def manifest_to_yaml_entry(manifest: OwlAgentManifest) -> dict[str, Any]:
    """Serialise a manifest into a minimal YAML-friendly dict for persistence."""
    entry: dict[str, Any] = {
        "name": manifest.name,
        "role": manifest.role,
        "system_prompt": manifest.system_prompt,
        "model_tier": manifest.model_tier,
        "temperature": manifest.temperature,
    }
    # UniOwl (ADR-A/B): human name + lifecycle/trigger. Written only when non-default
    # so existing on-demand owls keep byte-identical yaml entries.
    if manifest.display_name:
        entry["display_name"] = manifest.display_name
    # Additive: written only when non-default so existing owls keep byte-identical
    # yaml entries (mirrors the display_name/lifecycle conditional-write pattern).
    if manifest.boundaries:
        entry["boundaries"] = manifest.boundaries
    if manifest.evolution_strategy != "adaptive":
        entry["evolution_strategy"] = manifest.evolution_strategy
    if manifest.lifecycle != "on_demand":
        entry["lifecycle"] = manifest.lifecycle
    if manifest.trigger is not None:
        entry["trigger"] = manifest.trigger.model_dump(mode="json", exclude_none=True)
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
