"""SandboxAssembly — wires the sandbox backend selector + governor (OPS-5 / F149).

Extracted verbatim from the ~1700-line ``_phase_gateway`` monolith so the
code-execution trust boundary is wired in ONE cohesive, seam-testable unit
instead of inline in the boot method. Mirrors :class:`MemoryAssembly` /
:class:`SchedulerAssembly` / :class:`NotificationAssembly`.

The selector is the KEYSTONE code-execution trust boundary: ONE DI singleton
built from ``settings.sandbox`` — the rootless bwrap backend (PRIMARY) plus the
network-capable Docker backend, each gated by its enabled flag (a disabled
backend reports unavailable and is never picked). The ``execute_code`` tool reads
the selector off services at execute time; if neither backend is viable it
returns a structured unavailable and the tool NEVER runs code on the host.

The governor bounds total concurrent sandbox runs so N runs × the per-run memory
cap cannot OOM the host. Building it here also registers the recurring
``sandbox_sweep`` GC handler (leaked scratch dirs / containers / cgroup scopes).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.sandbox.governor import SandboxGovernor
    from stackowl.sandbox.selector import SandboxSelector


@dataclass(frozen=True)
class SandboxComponents:
    """Frozen container for the wired sandbox subsystem."""

    selector: SandboxSelector
    governor: SandboxGovernor


class SandboxAssembly:
    """Factory that wires the sandbox selector + governor from settings."""

    @staticmethod
    def build(settings: Settings) -> SandboxComponents:
        """Build the sandbox selector + governor and register the GC sweep handler."""
        log.infra.info("[sandbox] assembly.build: entry")

        # Deferred imports — keep this module cheap when execute_code is unused.
        from stackowl.sandbox.bwrap import BwrapSandbox
        from stackowl.sandbox.docker import DockerSandbox
        from stackowl.sandbox.governor import SandboxGovernor
        from stackowl.sandbox.selector import SandboxSelector
        from stackowl.scheduler.handlers.sandbox_sweep import (
            register_sandbox_sweep_handler,
        )

        selector = SandboxSelector(
            backends=[
                BwrapSandbox(enabled=settings.sandbox.bwrap_enabled),
                DockerSandbox(enabled=settings.sandbox.docker_enabled),
            ]
        )
        log.infra.debug(
            "[sandbox] assembly.build: selector wired",
            extra={
                "_fields": {
                    "bwrap_enabled": settings.sandbox.bwrap_enabled,
                    "docker_enabled": settings.sandbox.docker_enabled,
                }
            },
        )

        governor = SandboxGovernor()
        register_sandbox_sweep_handler()

        log.infra.info("[sandbox] assembly.build: exit — selector + governor ready")
        return SandboxComponents(selector=selector, governor=governor)
