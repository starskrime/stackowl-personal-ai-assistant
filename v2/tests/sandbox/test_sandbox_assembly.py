"""OPS-5 (F149) — SandboxAssembly seam test.

The sandbox wiring was extracted from the _phase_gateway monolith into a
unit-testable assembly. This asserts the seam builds a selector + governor from
settings (honoring the per-backend enabled flags) without booting the gateway.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from stackowl.sandbox.assembly import SandboxAssembly, SandboxComponents
from stackowl.sandbox.governor import SandboxGovernor
from stackowl.sandbox.selector import SandboxSelector


def _settings(*, bwrap: bool, docker: bool) -> MagicMock:
    s = MagicMock()
    s.sandbox.bwrap_enabled = bwrap
    s.sandbox.docker_enabled = docker
    return s


def test_build_returns_selector_and_governor() -> None:
    components = SandboxAssembly.build(_settings(bwrap=True, docker=False))
    assert isinstance(components, SandboxComponents)
    assert isinstance(components.selector, SandboxSelector)
    assert isinstance(components.governor, SandboxGovernor)


def test_build_honors_disabled_backends() -> None:
    # Both backends disabled — every wired backend must carry the disabled gate so
    # the selector can never run code on the host.
    components = SandboxAssembly.build(_settings(bwrap=False, docker=False))
    assert isinstance(components.selector, SandboxSelector)
    backends = components.selector._backends  # noqa: SLF001 — seam assertion
    assert backends, "selector must be wired with backends"
    assert all(b._enabled is False for b in backends), (  # noqa: SLF001
        "both backends must be gated off when settings disable them"
    )


def test_build_enables_backends_per_settings() -> None:
    components = SandboxAssembly.build(_settings(bwrap=True, docker=True))
    backends = components.selector._backends  # noqa: SLF001 — seam assertion
    assert all(b._enabled is True for b in backends)  # noqa: SLF001
