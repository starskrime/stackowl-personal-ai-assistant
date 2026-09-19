"""``CommandSpecRegistry`` -- the closed table of declared command types
(Story 4.3, AD-1).

Dict-based, mirrors ``commands/registry.py::CommandRegistry`` and
``journal/registry.py::EventRegistry``'s own style: ``register()`` raises on a
duplicate declaration (two registrations for one ``command_type`` is exactly
the "one type meaning two things" shape those two registries already refuse),
and lookup raises :class:`~stackowl.commands.spec.errors.
CommandTypeNotDeclaredError` rather than returning ``None`` -- AD-1: "there is
no generic 'run any command' endpoint", so an unregistered type is a loud
refusal, never a silent miss.
"""

from __future__ import annotations

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.errors import CommandTypeNotDeclaredError

# AD-7 — this package imports only `authz/` + `pipeline/durable`, never a
# SUBSYSTEM. `stackowl.infra` is cross-cutting infrastructure (logging), the
# same category as `pydantic` — never a subsystem a future story could delete
# out from under this package — so it is exempted the same way
# (tests/commands/spec/test_one_door_tripwires.py's import-boundary allowlist).
from stackowl.infra.observability import log


class CommandSpecRegistry:
    """Process-wide singleton table of every declared :class:`CommandSpec`."""

    _specs: dict[str, CommandSpec] = {}

    @classmethod
    def register(cls, spec: CommandSpec) -> CommandSpec:
        if spec.command_type in cls._specs:
            raise ValueError(
                f"CommandSpec already registered for {spec.command_type!r} -- "
                "a second registration would mean the same command type means "
                "two different things"
            )
        cls._specs[spec.command_type] = spec
        log.gateway.debug(
            "[commands] CommandSpecRegistry.register: registered",
            extra={"_fields": {
                "command_type": spec.command_type, "severity": spec.severity,
                "reversible": spec.reversible,
            }},
        )
        return spec

    @classmethod
    def get(cls, command_type: str) -> CommandSpec:
        spec = cls._specs.get(command_type)
        if spec is None:
            raise CommandTypeNotDeclaredError(command_type)
        return spec

    @classmethod
    def list(cls) -> list[CommandSpec]:
        return sorted(cls._specs.values(), key=lambda s: s.command_type)

    @classmethod
    def reset(cls) -> None:
        """Clear every declaration -- test use only."""
        cls._specs.clear()
