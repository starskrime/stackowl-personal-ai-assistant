"""Knowledge-tool security substrate (E4).

Shared, tool-agnostic helpers that the E4 self-mutation tools (``memory``,
``skill_manage``) consume before performing any write:

* :mod:`~stackowl.tools.knowledge.guards` — ``agent_self`` source-type constant
  and the non-interactive default-deny guard.
* :mod:`~stackowl.tools.knowledge.skill_validation` — skill frontmatter/name/
  category validators and the hard static security-scan gate.

The audit + snapshot + diff/restore provenance chokepoints live in the existing
command helper modules (``commands/skill_helpers.py`` :func:`record_skill_mutation`
and ``commands/memory_helpers.py`` :func:`forget_fact`) so the slash-command and
tool paths share exactly one mutation-with-provenance path.
"""

from __future__ import annotations

from stackowl.tools.knowledge.guards import (
    AGENT_SELF_SOURCE_TYPE,
    GuardDecision,
    deny_if_non_interactive,
)
from stackowl.tools.knowledge.skill_validation import (
    ScanResult,
    scan_skill_dir,
    security_scan_gate,
    validate_category,
    validate_content_size,
    validate_frontmatter,
    validate_skill_name,
)

__all__ = [
    "AGENT_SELF_SOURCE_TYPE",
    "GuardDecision",
    "ScanResult",
    "deny_if_non_interactive",
    "scan_skill_dir",
    "security_scan_gate",
    "validate_category",
    "validate_content_size",
    "validate_frontmatter",
    "validate_skill_name",
]
