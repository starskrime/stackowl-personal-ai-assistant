"""SandboxSelector — resolve WHICH backend runs a spec, BWRAP-PRIMARY policy.

LOCKED operator decision (bwrap-primary):

    * A run that needs NO network and where bwrap is viable → **bwrap** (the
      rootless, daemonless default — ideal on the ARM64 host and for Linux users
      without Docker).
    * A run that needs network → **Docker** (bwrap cannot grant network egress
      safely), but ONLY if Docker is viable; otherwise a structured refusal
      ("this run needs network, which requires Docker, and Docker is unavailable").
    * bwrap not viable but Docker viable → **Docker** (covers the no-network run
      too — Docker can isolate it with ``--network=none``).
    * Neither viable → a structured, ACTIONABLE "no sandbox backend available —
      install bubblewrap (bwrap) or Docker".

The selector NEVER raises (B5): every dead-end is a structured
:class:`SandboxSelection` carrying a reason, so the tool (E11-S5) degrades calmly
where no sandbox exists instead of crashing — and crucially NEVER falls back to
running code on the bare host.

How the real backends plug in (E11-S2 / E11-S3): the selector takes a list of
constructed :class:`SandboxBackend` instances (injectable for tests). It first
narrows that registry to the backends the host PROBE confirms viable, then applies
the bwrap-primary policy by backend ``name`` / ``is_rootless`` / ``supports_network``.
S2 registers a ``DockerSandbox`` and S3 a ``BwrapSandbox``; no selector change is
needed — they appear in the injected list and are matched by capability + policy.
The probe is injectable too, so tests drive the policy without touching the host.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.sandbox.base import SandboxBackend
from stackowl.sandbox.capability import SandboxCapability, SandboxProbe
from stackowl.sandbox.spec import ExecSpec

__all__ = ["SandboxSelection", "SandboxSelector"]


@dataclass(frozen=True)
class SandboxSelection:
    """The outcome of backend selection.

    Exactly one of ``backend`` (resolved) or ``reason`` (unavailable) is set.
    """

    backend: SandboxBackend | None
    reason: str | None

    @property
    def available(self) -> bool:
        return self.backend is not None

    @classmethod
    def found(cls, backend: SandboxBackend) -> SandboxSelection:
        return cls(backend=backend, reason=None)

    @classmethod
    def unavailable(cls, reason: str) -> SandboxSelection:
        return cls(backend=None, reason=reason)


class SandboxSelector:
    """Resolves a backend for a spec, bwrap-primary; structured-unavailable else."""

    def __init__(
        self,
        backends: Sequence[SandboxBackend],
        *,
        probe: SandboxProbe | None = None,
    ) -> None:
        # Backends are injected (S2/S3 register their real instances; tests pass
        # fakes). The probe is injectable so tests drive the policy deterministically
        # without touching the host; absent → probe the real host (never raises).
        self._backends = tuple(backends)
        self._probe = probe if probe is not None else SandboxCapability.probe()

    def select(self, spec: ExecSpec) -> SandboxSelection:
        """Resolve the backend for ``spec`` per the bwrap-primary policy. Never raises.

        Network-requiring runs go to Docker; no-network runs prefer rootless bwrap
        and fall back to Docker. Nothing viable → an actionable structured refusal.
        """
        # 1. ENTRY
        log.tool.debug(
            "[sandbox.selector] select: entry",
            extra={"_fields": {"network": spec.network, "language": spec.language}},
        )

        bwrap = self._viable_backend(rootless=True, network_capable=False)
        docker = self._viable_backend(rootless=False, network_capable=True)

        # 2. DECISION — network-requiring runs need Docker (bwrap can't grant net).
        if spec.network:
            if docker is not None:
                log.tool.info("[sandbox.selector] select: chose Docker (network requested)")
                return SandboxSelection.found(docker)
            return SandboxSelection.unavailable(
                "this run requests network access, which requires Docker "
                f"(rootless bwrap cannot grant network safely), but Docker is "
                f"unavailable: {self._probe.docker_reason}. Start the Docker daemon "
                f"or run without network."
            )

        # 3. NO-NETWORK — bwrap-primary: prefer the rootless default.
        if bwrap is not None:
            log.tool.info("[sandbox.selector] select: chose bwrap (rootless primary)")
            return SandboxSelection.found(bwrap)
        if docker is not None:
            log.tool.info(
                "[sandbox.selector] select: chose Docker (bwrap unavailable, no-network)"
            )
            return SandboxSelection.found(docker)

        # 4. EXIT — neither viable → actionable, structured unavailable.
        log.tool.info("[sandbox.selector] select: no sandbox backend available")
        return SandboxSelection.unavailable(
            "no sandbox backend available — install bubblewrap (bwrap) for "
            "rootless code execution, or Docker for network-capable runs. "
            f"Details: bwrap — {self._probe.bwrap_reason}; "
            f"docker — {self._probe.docker_reason}."
        )

    # --------------------------------------------------------------- internals
    def _viable_backend(
        self, *, rootless: bool, network_capable: bool
    ) -> SandboxBackend | None:
        """Pick a registered backend matching the role AND confirmed viable by probe.

        ``rootless`` selects the bwrap-style backend; ``network_capable`` the
        Docker-style one. A backend is only returned when the host probe confirms
        its primitive is viable, so an injected-but-unusable backend is skipped.
        """
        probe_ok = self._probe.bwrap_viable if rootless else self._probe.docker_viable
        if not probe_ok:
            return None
        for backend in self._backends:
            if backend.is_rootless == rootless and backend.supports_network == network_capable:
                return backend
        return None
