"""Logging helpers for browser tools — URL scrubbing, content redaction."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Defense-in-depth credential scrubbing for error-context strings.
# Each pattern matches a header/JSON-style assignment and replaces the value.
_CRED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]\s*[^\s;,'\"]+"), r"\1=***"),
    (re.compile(r"(?i)\b(authorization|bearer)\s*[:=]?\s*[A-Za-z0-9._\-+/=]{16,}"), r"\1=***"),
    (re.compile(r"(?i)\b(api[_-]?key|apikey|access[_-]?token|secret|password)"
                r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._\-+/=]{8,}[\"']?"), r"\1=***"),
)


def url_path_only(url: str) -> str:
    """Strip query strings and fragments. Returns ``scheme://netloc/path``.

    Used in every browser tool's INFO/DEBUG log to honor the
    'Log URL path only' rule (CLAUDE.md). Defense in depth: the
    SensitiveFieldFilter in infra/observability.py performs the same
    stripping as a second layer.
    """
    try:
        u = urlparse(url)
    except Exception:
        return "<unparsable-url>"
    if not u.scheme or not u.netloc:
        return url.split("?", 1)[0].split("#", 1)[0]
    return f"{u.scheme}://{u.netloc}{u.path}"


def _scrub_credentials(text: str) -> str:
    """Best-effort scrub of cookie / bearer / api-key style assignments."""
    out = text
    for pattern, replacement in _CRED_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def truncate_for_error(text: str, limit: int = 200) -> str:
    """Used only when logging error context — content is otherwise never logged.

    Scrubs common credential patterns before truncating, so a stack trace that
    happens to include a Set-Cookie header won't leak the value into the log.
    """
    scrubbed = _scrub_credentials(text)
    if len(scrubbed) <= limit:
        return scrubbed
    return scrubbed[:limit] + "...<truncated>"
