"""Agent-facing envelope for owl_build. Deliberately carries NO authority fields
(origin/created_by/creation_ceiling/bounds) — the tool forces those server-side."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class OwlBuildSpec(BaseModel):
    # extra="ignore" (not "forbid"): a stray field the weak model invented (e.g.
    # 'prompt'/'priority' bled in from a different tool schema) should be silently
    # dropped, not reject the whole otherwise-valid request. This does not widen
    # what the agent can control — authority fields (origin/created_by/
    # creation_ceiling/bounds) are still forced server-side regardless of what an
    # extra field claims; they are simply absent from the validated spec either way.
    model_config = ConfigDict(frozen=True, extra="ignore")

    action: Literal["create", "edit", "retire", "rename", "pause", "resume"]
    # name defaults to "" (not required): a free-text `/owl create <sentence>`
    # constructs a spec with no name so the validator reports it as a recoverable
    # MissingField and the tool ASKS for it (elicitation). Every action's own
    # hard-error branch in validate_owl_build_spec still rejects an empty name.
    name: str = ""
    preset: str | None = None
    explicit_tools: list[str] | None = None
    specialty: str | None = None
    model_tier: str | None = None
    # Cosmetic-only (owl rename) — the human-facing label shown/spoken instead of the
    # internal routing name. Separate from every other field: renaming never touches
    # tools/authority/schedule, so it is exempt from the no-edit-your-betters gate that
    # blocks edit/retire on the secretary/builtin personas (see OwlAgentManifest.spoken_name
    # / can_rename in owl_build.py).
    display_name: str | None = None
    # UniOwl schedule slot (ADR-T4 / TS8): a recurring cadence makes this owl a
    # SCHEDULED persona woken by a CronTrigger. ``schedule`` is the platform cadence
    # ("every 2h" / "every 30m" / "daily@09:00" / 5-field cron); ``goal`` is the
    # recurring instruction run each tick (defaults to ``specialty``); ``lifecycle``
    # lets the caller mark a recurring intent even before naming a cadence — then the
    # cadence is ASKED for via the resumable clarify path. All additive + optional, so
    # an on-demand create is byte-identical (no schedule ⇒ no trigger ⇒ on_demand).
    schedule: str | None = None
    goal: str | None = None
    lifecycle: Literal["on_demand", "scheduled"] | None = None
    # Free-text behavioural guardrail (design decision 4) — distinct from tool
    # grants; forwarded to the manifest and folded into the system prompt.
    boundaries: str | None = None
    # Preset evolution aggressiveness (design decision 3). All optional +
    # default-safe: an on-demand create omitting both is byte-identical.
    evolution_strategy: Literal["conservative", "adaptive", "experimental"] | None = None

    @field_validator("explicit_tools", mode="before")
    @classmethod
    def _coerce_json_string_list(cls, v: object) -> object:
        """qwen3.5-class models sometimes serialize a list arg as a JSON string
        ('["memory", "owl_build"]') instead of a real list. Coerce it back before
        strict list-typing rejects it. Anything else (already a list, None, a
        non-JSON string) passes through unchanged — validation errors on it
        normally, unchanged behavior."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return v
            if isinstance(parsed, list):
                return parsed
        return v


@dataclass(frozen=True)
class MissingFields:
    """A create spec that is underspecified but RECOVERABLE by asking the user.

    ``fields`` are the still-missing required field names (validator-decided — the
    state machine, not the LLM); ``partial`` is the spec so far. The tool rides
    ``partial`` through the ClarifyGateway resume, merges each answer, and
    re-validates until the schema is satisfied. Distinct from a hard error string
    (an INVALID value — asking cannot fix it)."""

    fields: tuple[str, ...]
    partial: OwlBuildSpec


def validate_owl_build_spec(spec: OwlBuildSpec) -> str | MissingFields | None:
    """Structured validation. Returns one of three results, never raises:

    * ``str`` — a HARD error (an invalid value; asking the user cannot fix it).
    * :class:`MissingFields` — recoverable: required ``create`` field(s) absent →
      the tool should ASK the user, merge, and re-validate (ADR-A state machine).
    * ``None`` — the spec is complete and valid.

    For ``create`` the irreducible required set is: a ``name``, a capability
    (``preset`` OR ``explicit_tools``), and a ``specialty``.
    """
    if spec.action in ("retire", "pause", "resume"):
        # Cadence-only or removal actions need nothing but a name (design
        # decision 2: pause/resume touch no tools/authority/persona).
        if not spec.name or not spec.name.strip():
            return "owl name is required."
        return None
    if spec.action == "edit":
        if not spec.name or not spec.name.strip():
            return "owl name is required."
        if spec.preset and spec.explicit_tools:
            return "provide either 'preset' or 'explicit_tools', not both."
        return None
    if spec.action == "rename":
        if not spec.name or not spec.name.strip():
            return "owl name is required."
        if not spec.display_name or not spec.display_name.strip():
            return "display_name is required to rename an owl."
        return None
    # create — preset XOR explicit_tools is an INVALID value (hard error); the
    # remaining gaps are RECOVERABLE missing required fields (ask the user).
    if spec.preset and spec.explicit_tools:
        return "provide either 'preset' or 'explicit_tools', not both."
    # Schedule slot (TS8): a PRESENT cadence must be a valid, within-floor schedule.
    # An invalid format / a sub-floor cadence is a HARD value error (re-asking the
    # same slot cannot fix a malformed value), surfaced clearly — never a crash. The
    # floor refusal reuses the SAME guard the manifest validator enforces.
    sched = (spec.schedule or "").strip()
    if sched:
        sched_err = _schedule_value_error(sched)
        if sched_err is not None:
            return sched_err
    missing: list[str] = []
    if not spec.name or not spec.name.strip():
        missing.append("name")
    if not spec.preset and not spec.explicit_tools:
        missing.append("capability")
    if not spec.specialty or not spec.specialty.strip():
        missing.append("specialty")
    # A recurring owl needs a cadence: ``lifecycle='scheduled'`` with no schedule is
    # a RECOVERABLE gap → ASK for it via the resumable clarify path (reuses
    # _elicit_missing — no parallel flow). Detecting "recurring intent" is the
    # model's job (it sets lifecycle), never a keyword scan here.
    if spec.lifecycle == "scheduled" and not sched:
        missing.append("schedule")
    if missing:
        return MissingFields(fields=tuple(missing), partial=spec)
    return None


def _schedule_value_error(schedule: str) -> str | None:
    """Validate a present schedule cadence; return a clear error or ``None``.

    Reuses the scheduler's own validator (:func:`is_valid_schedule`) so owl_build can
    never advertise a cadence the scheduler then mis-arms, and the interval-floor
    guard (:func:`interval_floor_error`) so a too-fast schedule is refused with the
    same message the manifest validator would raise — surfaced as a structured error
    instead of a manifest-construction crash. Lazy imports keep this envelope module
    free of a scheduler import cycle (mirrors the manifest validator's pattern).
    """
    from stackowl.owls.owl_schedule_guards import interval_floor_error
    from stackowl.tools.scheduling.cron_helpers import is_valid_schedule

    if not is_valid_schedule(schedule):
        return (
            f"'{schedule}' is not a valid schedule — use a cadence like 'every 2h', "
            "'every 30m', 'daily@09:00', or a 5-field cron expression."
        )
    return interval_floor_error(schedule)
