"""Severities and the principal that holds them.

This is the platform's shared authorization vocabulary — the one home Epic 4's
"one door" command gate (AD-1, AD-27) needs so every later authz surface
(`commands/spec/`, the action-policy gate, standing authority) can depend on it
without importing `control_plane`, which AD-7 deletes later. It used to live in
`control_plane/auth.py`; `control_plane/auth.py` now re-exports these five
symbols from here rather than defining them, so every existing import path
keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: The severities an endpoint can declare, in the platform's own vocabulary —
#: `ToolManifest.action_severity` is `Literal["read","write","consequential"]`.
#: Reusing it means "this credential may read but not write" is expressible on
#: day one with no new concepts, rather than needing a rewrite when it is asked
#: for. A boolean `authenticated` is the recorded core defect of this programme —
#: "consent gated ACTIONS, nothing gated AUTHORITY" — with a new front door.
READ: Final = "read"
WRITE: Final = "write"
CONSEQUENTIAL: Final = "consequential"
ALL_SEVERITIES: Final = frozenset({READ, WRITE, CONSEQUENTIAL})


@dataclass(frozen=True)
class ControlPrincipal:
    """Who is calling, and what they are allowed to do.

    `granted` is a SET even though it currently always holds all three
    severities. That is deliberate: a boolean would have to become a set later,
    and every handler written against the boolean would have to change. The set
    costs nothing now and is the whole difference between authenticating a
    CONNECTION and authorising a PERSON.
    """

    principal_id: str
    credential_id: str
    granted: frozenset[str]

    def may(self, severity: str) -> bool:
        """Whether this principal may invoke an endpoint of *severity*."""
        return severity in self.granted
