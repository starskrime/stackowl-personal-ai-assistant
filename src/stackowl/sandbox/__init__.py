"""sandbox — the code-execution trust boundary (E11 KEYSTONE).

An INDEPENDENT trust boundary that runs untrusted / LLM-generated code in OS-level
isolation (E11-S2 Docker, E11-S3 bwrap). This package owns the contract
(:class:`SandboxBackend`), the request/result value models
(:class:`ExecSpec` / :class:`ResourceCaps` / :class:`ExecResult`), the host
capability probe, and the bwrap-primary selector.

Deliberately self-contained: it MUST NOT import :mod:`stackowl.tools` — the tool
that exposes code execution (E11-S5) depends on this package, never the reverse,
so the isolation layer cannot be entangled with the tool surface it guards.
"""

from __future__ import annotations

from stackowl.sandbox.base import SandboxAvailability, SandboxBackend
from stackowl.sandbox.bwrap import BwrapSandbox
from stackowl.sandbox.capability import SandboxCapability, SandboxProbe
from stackowl.sandbox.selector import SandboxSelection, SandboxSelector
from stackowl.sandbox.spec import ExecResult, ExecSpec, ExitReason, ResourceCaps

__all__ = [
    "BwrapSandbox",
    "ExecResult",
    "ExecSpec",
    "ExitReason",
    "ResourceCaps",
    "SandboxAvailability",
    "SandboxBackend",
    "SandboxCapability",
    "SandboxProbe",
    "SandboxSelection",
    "SandboxSelector",
]
