"""Live Path Census — a fail-closed reachability law for registered subsystems.

The recurring defect class in this codebase is "registered ≠ reachable": a
subsystem is built and unit-tested in isolation, ships green, but its activation
gate is dead on the path the default owl + default config actually run (e.g. the
skills injector gated on a manifest field the default owl never sets). Unit tests
sit INSIDE a component; nothing asserts the seam is live on the default path.

The census makes that a standing, executable invariant: every registered
subsystem declares a ``reachability_probe`` answering "on the default owl + default
config, is my activation seam live?" — and one fail-closed test runs them all.
See :mod:`stackowl.health.reachability.census` for the registry and
:mod:`stackowl.health.reachability.probes` for the probes.
"""

from __future__ import annotations

from stackowl.health.reachability.census import (
    ProbeResult,
    REQUIRED_PROBES,
    census_passes,
    reachability_probe,
    registered_probes,
    run_census,
)

__all__ = [
    "ProbeResult",
    "REQUIRED_PROBES",
    "census_passes",
    "reachability_probe",
    "registered_probes",
    "run_census",
]
