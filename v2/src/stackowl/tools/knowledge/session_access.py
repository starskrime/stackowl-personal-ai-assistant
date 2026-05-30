"""Shared substrate for the session-conversation read tools.

``session_search`` (E4-S5) and ``transcripts`` (E4-S6) both read prior session
conversation turns out of the canonical SQLite conversation store (the
``messages`` + ``conversations`` tables, migration 0002 — reached via raw
:class:`~stackowl.db.pool.DbPool` reads, the SAME store the fact-extraction
handler already queries). They therefore SHARE two security concerns, which
live here so there is exactly one implementation of each:

* :func:`redact_secrets` — value-level masking of secrets that may be embedded
  in *returned* transcript text (a prior turn might literally contain an API
  key the user pasted). This is distinct from the observability filter, which
  redacts log-field *keys* by name; here we must scrub secret *values* out of
  free text before it re-enters the model's context. The patterns mirror the
  secret-shapes the skill threat-scanner already catalogues (``sk-`` provider
  keys, ``key=…`` / ``token: …`` assignments, ``Bearer`` headers) so the two
  surfaces agree on what "a secret" looks like.
* :func:`resolve_visibility` — the cross-session visibility guard. By default a
  caller may only read its OWN current session (``TraceContext.session_id``).
  Reading a *different* session is permitted only when that session has the
  SAME owner (``conversations.owl_name``) as the caller's current session;
  cross-owner reads are refused. This stops one owl replaying another owl's (or
  another tenant's) private conversation.

Both helpers FAIL CLOSED: an indeterminate owner, a missing session, or a
scanner that errors yields no data / a structured refusal — never a raise and
never an over-broad read.

Provenance / port-vs-build: HYBRID. The three-shape recall surface (browse /
keyword-discover / scroll-around-anchor) and the current-session-exclusion idea
are ported from prior agent art and re-expressed neutrally onto our SQLite
store; the value-masking redactor and the owner-scoped visibility guard are
BUILT for this codebase's multi-owl model. See
``_bmad-output/research/tool-port-analysis.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

# --------------------------------------------------------------------- redaction

_REDACTION_PLACEHOLDER = "[REDACTED]"

# Secret value-shapes to mask out of returned transcript text. These mirror the
# credential shapes the skill threat-scanner flags, expressed as *value*
# matchers (not arg-key matchers). \p{...}-free because Python's stdlib ``re``
# has no \p — we use explicit classes that still cover non-ASCII-free secret
# alphabets (secrets are base62/64/hex by construction).
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Provider-style keys: an sk-/pk-/rk- prefix then a long token body.
    re.compile(r"\b[a-z]{2}-[A-Za-z0-9_-]{20,}\b"),
    # GitHub-style tokens (ghp_/gho_/ghs_/ghr_ + body).
    re.compile(r"\bgh[a-z]_[A-Za-z0-9]{20,}\b"),
    # Bearer / Authorization header values.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    # key=VALUE / token: VALUE / password = VALUE assignments (quoted or bare).
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd|credential)s?"
        r"\s*[=:]\s*['\"]?([A-Za-z0-9+/=_\-]{8,})['\"]?"
    ),
    # JWTs: three base64url segments separated by dots.
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    # AWS access key id (AKIA + 16 upper/digits).
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Slack tokens (xoxb-/xoxp-/xoxa-/xoxr-/xoxs- + body).
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    # PEM private-key blocks (whole block).
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    # Basic-auth credentials embedded in a URL (mask the password group).
    re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:([^/\s:@]+)@"),
)


def redact_secrets(text: str) -> str:
    """Mask secret-shaped substrings in ``text`` before it re-enters context.

    Applied to EVERY turn returned by either session tool, including partial
    results. Fails closed: if scanning raises, the whole field is replaced with
    the placeholder rather than leaking an unscanned secret.
    """
    if not text:
        return text
    try:
        out = text
        for pattern in _SECRET_PATTERNS:
            # For assignment-style patterns we keep the key label and mask only
            # the value group; for standalone-token patterns we mask the whole
            # match. ``_mask`` handles both by inspecting the group count.
            out = pattern.sub(_mask, out)
        return out
    except Exception as exc:  # fail-closed — never leak an unscanned secret
        log.tool.error(
            "session_access.redact_secrets: scan failed — masking whole field",
            exc_info=exc,
        )
        return _REDACTION_PLACEHOLDER


def _mask(match: re.Match[str]) -> str:
    """Replace a secret match, preserving a leading ``key=`` label if present."""
    if match.groups():
        # Assignment-style: rebuild prefix (everything up to the value) + mask.
        value = match.group(1)
        whole = match.group(0)
        prefix = whole[: whole.rfind(value)]
        return f"{prefix}{_REDACTION_PLACEHOLDER}"
    return _REDACTION_PLACEHOLDER


# ------------------------------------------------------------------- visibility


@dataclass(frozen=True)
class VisibilityDecision:
    """Outcome of the cross-session visibility guard.

    ``allowed`` is the only field a caller must branch on; ``reason`` is a
    short, user-surfaceable refusal the tool folds into its structured result.
    ``session_id`` is the (possibly defaulted-to-current) session the caller is
    cleared to read.
    """

    allowed: bool
    session_id: str | None = None
    reason: str = ""


_OWNER_SQL = "SELECT DISTINCT owl_name FROM conversations WHERE session_id = ? LIMIT 2"


async def _session_owner(db: DbPool, session_id: str) -> str | None:
    """Return the single owning owl for ``session_id``, or None if unknown/ambiguous."""
    rows = await db.fetch_all(_OWNER_SQL, (session_id,))
    if len(rows) != 1:
        # 0 rows → unknown session; >1 owner → ambiguous, fail closed.
        return None
    owner = rows[0].get("owl_name")
    return owner if isinstance(owner, str) and owner else None


async def resolve_visibility(
    db: DbPool, requested_session_id: str | None,
) -> VisibilityDecision:
    """Decide whether the caller may read ``requested_session_id``.

    Policy (fail-closed, owner-scoped):

    * No ``requested_session_id`` → default to the caller's CURRENT session
      (``TraceContext.session_id``). If there is no current session either,
      refuse — there is nothing to scope a read to.
    * ``requested_session_id`` == current session → always allowed (own data).
    * A different session → allowed ONLY if it has the SAME owner
      (``owl_name``) as the caller's current session. Cross-owner and
      unknown-owner reads are refused.
    """
    current = TraceContext.get().get("session_id")
    requested = (requested_session_id or "").strip() or None

    target = requested or current
    if not target:
        return VisibilityDecision(
            allowed=False,
            reason="no session in scope (no session_id given and no current session).",
        )

    # Reading own current session is always fine.
    if current is not None and target == current:
        return VisibilityDecision(allowed=True, session_id=target)

    # Cross-session: require same owner as the caller's current session.
    if current is None:
        return VisibilityDecision(
            allowed=False, session_id=target,
            reason=(
                f"refusing cross-session read of '{target}': no current session "
                "to authorize against."
            ),
        )

    current_owner = await _session_owner(db, current)
    target_owner = await _session_owner(db, target)
    if current_owner is None or target_owner is None:
        return VisibilityDecision(
            allowed=False, session_id=target,
            reason=(
                f"refusing cross-session read of '{target}': owner could not be "
                "determined (unknown or ambiguous session)."
            ),
        )
    if current_owner != target_owner:
        log.tool.warning(
            "session_access.resolve_visibility: cross-owner read blocked",
            extra={"_fields": {"current_owner": current_owner, "target": target}},
        )
        return VisibilityDecision(
            allowed=False, session_id=target,
            reason=(
                f"refusing cross-session read of '{target}': it belongs to a "
                "different owner than the current session."
            ),
        )
    return VisibilityDecision(allowed=True, session_id=target)
