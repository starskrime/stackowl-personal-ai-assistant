"""authz — owl capability bounds and authorization (Epic 2).

This package is the home for an owl's authorization model. Epic 2 Story 1 lands
the :class:`BoundsSpec` closed enumeration and enforces its TOOLS axis at the
dispatch seam; later stories (the authorization envelope, preflight planner, and
authorizer) build on it.
"""

from __future__ import annotations

from stackowl.authz.action_policy import (
    ActionPolicyDecision,
    ActionPolicyOutcome,
    attends,
    decide,
)
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
from stackowl.authz.requester import (
    RequesterKind,
    principal_for,
    requester_kind_from_trace,
)
from stackowl.authz.severity import (
    ALL_SEVERITIES,
    CONSEQUENTIAL,
    READ,
    WRITE,
    ControlPrincipal,
)
from stackowl.authz.state_change_census import (
    COMMAND_TYPE_MIGRATIONS,
    MIGRATION_STORIES,
    CommandTypeMigration,
)
from stackowl.authz.undo import (
    COMMAND_PRUNE_FLOOR_DAYS,
    UNDO_WINDOW,
    UNDO_WINDOW_DAYS,
    UndoDecision,
    decide_undo,
)

__all__ = [
    "ALL_SEVERITIES",
    "ActionPolicyDecision",
    "ActionPolicyOutcome",
    "BoundsSpec",
    "BoundsViolation",
    "CONSEQUENTIAL",
    "COMMAND_PRUNE_FLOOR_DAYS",
    "COMMAND_TYPE_MIGRATIONS",
    "CommandTypeMigration",
    "ControlPrincipal",
    "MIGRATION_STORIES",
    "NetworkRule",
    "READ",
    "RequesterKind",
    "ResourceCaps",
    "UNDO_WINDOW",
    "UNDO_WINDOW_DAYS",
    "UndoDecision",
    "WRITE",
    "attends",
    "check_effective_bounds",
    "check_tool_bounds",
    "decide",
    "decide_undo",
    "effective_bounds",
    "principal_for",
    "requester_kind_from_trace",
]
