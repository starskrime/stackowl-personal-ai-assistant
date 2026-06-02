"""SandboxBackend — the code-execution backend contract (E11 KEYSTONE).

A backend runs untrusted / LLM-generated code in ISOLATION and returns a fully
provenance-tagged :class:`~stackowl.sandbox.spec.ExecResult`. This module is the
contract ONLY — the concrete backends (E11-S2 Docker, E11-S3 bwrap) implement
:meth:`SandboxBackend.run`; here it is abstract.

INVARIANTS every backend MUST uphold (the load-bearing safety contract — a
backend that cannot guarantee one of these MUST refuse via a structured
``ExecResult(exit_reason="denied"|"sandbox_error")``, NEVER fall through to the
host):

1. **Never execute un-isolated on the host.** If isolation cannot be established,
   refuse — never degrade to a bare subprocess (that is what ``shell`` already is).
2. **Mandatory caps-or-refuse.** Every cap in ``spec.caps`` must be enforced. A
   backend unable to enforce a cap REFUSES; there is no uncapped run.
3. **Deny-all network unless opted in AND supported.** The child gets no network
   unless ``spec.network`` is True AND ``supports_network`` is True. A
   network-requesting spec sent to a non-network backend is a refusal, not a
   silent grant.
4. **Env allowlist-from-empty.** The child inherits ONLY the names in
   ``spec.env_allow`` (a minimal secret-free set by default) — never the host's
   full environment; secrets/tokens never cross the boundary.
5. **Never raise from :meth:`run`.** Every operational failure (timeout, OOM,
   provision error, denial) returns a structured :class:`ExecResult`; exceptions
   do not leak to the caller (B5 self-healing).
6. **No host filesystem access beyond declared mounts.** The child sees only its
   own scratch workdir (and any explicitly-declared, bounded mounts) — never the
   host root, ``~/.stackowl`` secrets, or the project tree by default. Host FS is
   read-only / inaccessible unless a mount is deliberately and narrowly granted.
7. **No privilege escalation.** The child runs unprivileged with
   ``no-new-privileges`` and dropped capabilities (Docker: ``--cap-drop=ALL`` +
   ``--security-opt no-new-privileges`` + seccomp default-deny; bwrap: rootless
   user-namespace, no setuid). A backend that cannot guarantee this refuses.

Future seam (E11-S4 PTC/RPC — NOT built here): a backend may later expose a
host-tool callback channel so code inside the sandbox can call a curated set of
host tools over RPC. The contract is shaped so that capability can be added as an
OPTIONAL backend feature (a future ``rpc_channel`` accessor / a flag on the spec)
WITHOUT changing :meth:`run`'s structured-result discipline — this story does not
implement PTC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from stackowl.sandbox.spec import ExecResult, ExecSpec

__all__ = ["SandboxAvailability", "SandboxBackend"]


@dataclass(frozen=True)
class SandboxAvailability:
    """Whether a backend can run right now, with a structured reason when not.

    Mirrors the image substrate's availability shape so the selector can skip a
    backend whose host probe failed WITHOUT any backend raising (B5).
    """

    available: bool
    reason: str | None = None

    @classmethod
    def ok(cls) -> SandboxAvailability:
        return cls(available=True, reason=None)

    @classmethod
    def no(cls, reason: str) -> SandboxAvailability:
        return cls(available=False, reason=reason)


class SandboxBackend(ABC):
    """Abstract isolated code-execution backend (E11 keystone trust boundary).

    Implementations MUST uphold the seven module-level INVARIANTS. In particular
    :meth:`run` NEVER raises for an operational failure — it returns a structured
    :class:`ExecResult` (the selector / tool surface it), and :meth:`is_available`
    likewise never raises.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable backend identifier (recorded as ``ExecResult.backend_used``)."""
        ...

    @property
    @abstractmethod
    def is_rootless(self) -> bool:
        """True → runs without a privileged daemon (rootless, e.g. bubblewrap)."""
        ...

    @property
    @abstractmethod
    def supports_network(self) -> bool:
        """True → CAN grant network egress when ``spec.network`` opts in.

        A backend returning False must REFUSE a ``network=True`` spec (invariant
        3) — the selector routes network-requiring runs to a network-capable
        backend instead.
        """
        ...

    @abstractmethod
    async def is_available(self) -> SandboxAvailability:
        """Report whether this backend can isolate a run now. Never raises (B5)."""
        ...

    @abstractmethod
    async def run(self, spec: ExecSpec) -> ExecResult:
        """Execute ``spec`` in isolation and return a provenance-tagged result.

        MUST honour every module-level invariant. NEVER raises for an operational
        failure — returns a structured :class:`ExecResult` instead (including a
        ``denied`` result when a cap cannot be enforced or a network request
        cannot be satisfied). Implemented by E11-S2 (Docker) / E11-S3 (bwrap);
        abstract here.
        """
        ...
