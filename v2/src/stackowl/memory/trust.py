"""Single source of truth: memory content trust tier, assigned MECHANICALLY from the source
channel (never the owl's judgment). Default 'untrusted' (fail-safe). 'manual' (the human /remember
+ telegram-confirm path) is the only producer of 'trusted'; agent-callable surfaces hardcode
'agent_self' (-> self) and are structurally incapable of submitting 'manual'. Story E."""
from __future__ import annotations

from typing import Literal

Trust = Literal["trusted", "self", "untrusted"]
SAFE_DEFAULT: Trust = "untrusted"

_SOURCE_TRUST: dict[str, Trust] = {
    "manual": "trusted",
    "agent_self": "self",
    "parliament": "self",
    "conversation": "self",
    "conversation_fact": "self",
    "webpage": "untrusted",
    "screenshot": "untrusted",
}


def trust_for_source(source_type: str) -> Trust:
    """Map a source_type to its trust tier. Unknown -> SAFE_DEFAULT (untrusted, fail-safe)."""
    return _SOURCE_TRUST.get(source_type, SAFE_DEFAULT)
