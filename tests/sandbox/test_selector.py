"""Tests for SandboxSelector — bwrap-primary policy + structured-unavailable.

Fake backends only; the probe is INJECTED so the policy is driven deterministically
without touching the host (no real bwrap/docker invoked).
"""

from __future__ import annotations

from stackowl.sandbox.base import SandboxAvailability, SandboxBackend
from stackowl.sandbox.capability import SandboxProbe
from stackowl.sandbox.selector import SandboxSelector
from stackowl.sandbox.spec import ExecResult, ExecSpec, ResourceCaps


class _FakeBackend(SandboxBackend):
    def __init__(self, name: str, *, rootless: bool, network: bool) -> None:
        self._name = name
        self._rootless = rootless
        self._network = network

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_rootless(self) -> bool:
        return self._rootless

    @property
    def supports_network(self) -> bool:
        return self._network

    async def is_available(self) -> SandboxAvailability:
        return SandboxAvailability.ok()

    async def run(self, spec: ExecSpec) -> ExecResult:  # pragma: no cover - not S1
        return ExecResult.error(
            reason="sandbox_error",
            message="not implemented in S1",
            backend_used=self._name,
            caps_applied=ResourceCaps(),
        )


def _bwrap() -> _FakeBackend:
    return _FakeBackend("bwrap", rootless=True, network=False)


def _docker() -> _FakeBackend:
    return _FakeBackend("docker", rootless=False, network=True)


def _probe(*, bwrap: bool, docker: bool) -> SandboxProbe:
    return SandboxProbe(
        bwrap_viable=bwrap,
        docker_viable=docker,
        bwrap_reason="bwrap" if bwrap else "bwrap not installed",
        docker_reason="docker" if docker else "docker unavailable",
        platform_supported=bwrap or docker,
    )


class TestBwrapPrimaryPolicy:
    def test_no_network_prefers_bwrap(self) -> None:
        sel = SandboxSelector([_bwrap(), _docker()], probe=_probe(bwrap=True, docker=True))
        out = sel.select(ExecSpec(code="print(1)"))
        assert out.available is True
        assert out.backend is not None
        assert out.backend.name == "bwrap"

    def test_network_routes_to_docker(self) -> None:
        sel = SandboxSelector([_bwrap(), _docker()], probe=_probe(bwrap=True, docker=True))
        out = sel.select(ExecSpec(code="print(1)", network=True))
        assert out.available is True
        assert out.backend is not None
        assert out.backend.name == "docker"

    def test_network_without_docker_is_structured_refusal(self) -> None:
        sel = SandboxSelector([_bwrap()], probe=_probe(bwrap=True, docker=False))
        out = sel.select(ExecSpec(code="print(1)", network=True))
        assert out.available is False
        assert out.reason is not None
        assert "network" in out.reason
        assert "Docker" in out.reason

    def test_no_bwrap_falls_back_to_docker_for_no_network(self) -> None:
        sel = SandboxSelector([_docker()], probe=_probe(bwrap=False, docker=True))
        out = sel.select(ExecSpec(code="print(1)"))
        assert out.available is True
        assert out.backend is not None
        assert out.backend.name == "docker"

    def test_neither_viable_is_actionable_unavailable(self) -> None:
        sel = SandboxSelector([], probe=_probe(bwrap=False, docker=False))
        out = sel.select(ExecSpec(code="print(1)"))
        assert out.available is False
        assert out.reason is not None
        assert "no sandbox backend available" in out.reason
        assert "bubblewrap" in out.reason
        assert "Docker" in out.reason

    def test_probe_viable_but_no_registered_backend_skips(self) -> None:
        # Probe says bwrap viable, but no bwrap instance registered → must not
        # return a wrong backend; with only docker present + no-network → docker.
        sel = SandboxSelector([_docker()], probe=_probe(bwrap=True, docker=True))
        out = sel.select(ExecSpec(code="print(1)"))
        assert out.available is True
        assert out.backend is not None
        assert out.backend.name == "docker"

    def test_never_raises(self) -> None:
        sel = SandboxSelector([], probe=_probe(bwrap=False, docker=False))
        # both network and no-network dead-ends return structured selections.
        assert sel.select(ExecSpec(code="x", network=True)).available is False
        assert sel.select(ExecSpec(code="x")).available is False
