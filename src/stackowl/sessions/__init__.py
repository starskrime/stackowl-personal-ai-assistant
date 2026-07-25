"""Session lifecycle — lanes, incarnations, and the boundaries between them.

Map item D01.7. Design: ``docs/hermes-mapping/designs/D01.7.md``.
"""

from stackowl.sessions.models import (
    Branch,
    ChatType,
    ResetReason,
    SessionEntry,
    SessionSource,
    build_session_key,
    is_shared_lane,
    new_entry,
    new_session_id,
)
from stackowl.sessions.policy import (
    ResetMode,
    ResetPolicy,
    Resolution,
    expired_reason,
    reset_notice,
    resolve,
    should_suspend_for_restart_loop,
)

__all__ = [
    "Branch", "ChatType", "ResetReason", "SessionEntry", "SessionSource",
    "build_session_key", "is_shared_lane", "new_entry", "new_session_id",
    "ResetMode", "ResetPolicy", "Resolution", "expired_reason", "reset_notice",
    "resolve", "should_suspend_for_restart_loop",
]
