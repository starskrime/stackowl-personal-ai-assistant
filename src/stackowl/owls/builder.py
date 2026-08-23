"""SpecialistOwlBuilder — the single pure constructor for owl manifests.

One lifecycle: derive (preset|explicit) -> validate -> instantiate. Pure: no I/O,
no persistence (the command layer persists). The generated persona is the owl's
"compass": it states the specialty AND tells the owl what to do when a request
falls outside it, so a narrow owl is additive and self-healing rather than a
dead-end.

ESC-34 (2026-08-23) changed WHAT it is told. The persona used to name
`delegate_task`; that tool left ROUTER_TOOLS because the bounds gate granted it so
a blocked owl could route around a limit while the task envelope refused it as
off-plan — two gates behaving as designed, with contradictory designs. A persona
that names a tool the owl no longer holds is the same defect one layer up, so the
compass now points at the APPEAL (owl_build) instead of the escape hatch."""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.authz.bounds import BoundsSpec
from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import ModelTier, OwlAgentManifest
from stackowl.owls.tool_presets import PRESETS, ROUTER_TOOLS


@dataclass(frozen=True)
class OwlSpec:
    """A build request. Provide a ``preset`` OR ``explicit_tools`` (not both);
    neither => an unbounded general owl (today's bare ``/owls add``).

    NOTE: an empty ``explicit_tools=()`` is treated as "no tools given" (=>
    unbounded), NOT as an explicit deny-all toolset. A deny-all owl is not a
    builder use-case in S1."""

    name: str
    role: str
    model_tier: ModelTier
    preset: str | None = None
    explicit_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    capability_profile: tuple[str, ...] = ()
    provider_name: str | None = None
    temperature: float = 0.7
    system_prompt: str | None = None
    specialty: str | None = None
    valid_tools: frozenset[str] | None = None


def generate_persona(name: str, role: str, specialty: str) -> str:
    """The compass + boundary-router instruction. Language-neutral."""
    return (
        f"Persona: {name}. Role: {role}. Specialty: {specialty}. "
        f"Handle {specialty} directly using your own tools. "
        f"For any request outside {specialty}, say so plainly and do not attempt "
        f"tools you do not have. If you need a capability you lack, ask for it with "
        f"owl_build action='grant' — that asks the operator to widen your bounds. "
        f"Respond in the language of the user."
    )


class SpecialistOwlBuilder:
    """Turns an :class:`OwlSpec` into a validated :class:`OwlAgentManifest`."""

    def build(self, spec: OwlSpec) -> OwlAgentManifest:
        log.startup.debug(
            "[owls] builder.build: entry",
            extra={"_fields": {"name": spec.name, "preset": spec.preset,
                               "explicit": len(spec.explicit_tools)}},
        )
        if spec.preset and spec.explicit_tools:
            raise ValueError("provide a preset OR explicit tools, not both")

        bounds: BoundsSpec | None = None
        capability_profile = list(spec.capability_profile)
        specialty = spec.specialty or spec.role

        base: frozenset[str] | None = None
        if spec.preset:
            if spec.preset not in PRESETS:
                raise ValueError(
                    f"unknown preset: {spec.preset!r} (known: {sorted(PRESETS)})"
                )
            preset = PRESETS[spec.preset]
            base = preset.tools
            specialty = spec.specialty or preset.specialty
            if not capability_profile:
                capability_profile = list(preset.capability_profile)
        elif spec.explicit_tools:
            base = frozenset(spec.explicit_tools)

        if base is not None:
            base = self._validate(base, spec.valid_tools)
            tools = base | ROUTER_TOOLS
            bounds = BoundsSpec(tools=tools)

        system_prompt = spec.system_prompt or generate_persona(spec.name, spec.role, specialty)
        manifest = OwlAgentManifest(
            name=spec.name,
            role=spec.role,
            system_prompt=system_prompt,
            model_tier=spec.model_tier,
            provider_name=spec.provider_name,
            temperature=spec.temperature,
            tools=sorted(bounds.tools) if bounds is not None and bounds.tools is not None else [],
            capability_profile=capability_profile,
            skills=spec.skills,
            bounds=bounds,
            dna=OwlDNA(),
        )
        log.startup.debug(
            "[owls] builder.build: exit",
            extra={"_fields": {"name": manifest.name, "bounded": bounds is not None}},
        )
        return manifest

    @staticmethod
    def _validate(requested: frozenset[str], valid: frozenset[str] | None) -> frozenset[str]:
        if valid is None:
            log.startup.warning(
                "[owls] builder._validate: no catalog — skipping tool validation (fail-open)",
                extra={"_fields": {"requested": len(requested)}},
            )
            return requested
        kept = requested & valid
        dropped = requested - valid
        if dropped:
            log.startup.warning(
                "[owls] builder._validate: dropped unknown tools",
                extra={"_fields": {"dropped": sorted(dropped)}},
            )
        return kept
