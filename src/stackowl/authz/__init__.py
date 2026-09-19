"""authz — owl capability bounds and authorization (Epic 2).

This package is the home for an owl's authorization model. Epic 2 Story 1 lands
the :class:`BoundsSpec` closed enumeration and enforces its TOOLS axis at the
dispatch seam; later stories (the authorization envelope, preflight planner, and
authorizer) build on it.
"""

from __future__ import annotations

from stackowl.authz.bounds import (
    BoundsSpec,
    BoundsViolation,
    NetworkRule,
    ResourceCaps,
)
from stackowl.authz.bounds_guard import (
    check_effective_bounds,
    check_tool_bounds,
    effective_bounds,
)
from stackowl.authz.severity import (
    ALL_SEVERITIES,
    CONSEQUENTIAL,
    READ,
    WRITE,
    ControlPrincipal,
)

__all__ = [
    "ALL_SEVERITIES",
    "BoundsSpec",
    "BoundsViolation",
    "CONSEQUENTIAL",
    "ControlPrincipal",
    "NetworkRule",
    "READ",
    "ResourceCaps",
    "WRITE",
    "check_effective_bounds",
    "check_tool_bounds",
    "effective_bounds",
]
