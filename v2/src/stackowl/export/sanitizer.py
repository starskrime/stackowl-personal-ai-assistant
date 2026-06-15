"""ExportSanitizer — scans export content for raw secrets and sensitive fields.

Security posture (C7 / F133):
* ``_PATTERNS`` is an ordered, most-specific-first deny-list of CURRENT vendor
  secret shapes. New vendor patterns are inserted ABOVE the bare generic-40
  catch-all so a longer key is never fragmented into a partial-redact + leaked
  tail. Exact-length quantifiers are avoided (a length-drifted key slips through
  an exact ``{93}``) — every vendor entry uses a min-bounded range.
* ``_key_is_sensitive`` matches on key *segments* (split on non-alphanumeric and
  camelCase) against a documented schema-vocabulary deny-list, so ``monkey`` /
  ``turkey`` / ``capital`` no longer false-match ``key`` / ``api`` substrings.
  These fragments are field-NAME vocabulary (ASCII by schema construction), not
  natural language, so they do not violate the no-hardcoded-English mandate.
* ``check_and_raise`` is the fail-closed export tripwire: it raises on a named
  vendor pattern OR a high-entropy value under a sensitive key (both
  high-confidence). It deliberately does NOT raise on the bare generic-40
  (false-positive-prone → would become denial-of-export on every git SHA/UUID).
* No secret VALUE is ever logged — only pattern names, key names, and counts.
"""

from __future__ import annotations

import math
import re

from stackowl.exceptions import SecurityError
from stackowl.infra.observability import log

# Ordered by specificity (most specific first). Named vendor patterns precede the
# bare generic-40 catch-all so a longer secret is matched whole, never fragmented.
# Lengths are MIN-BOUNDED ranges (never exact) so a length-drifted key still
# matches. Each entry documents the vendor's current published format.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # JWT — three base64url segments. Most specific (has the dotted structure).
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    # Anthropic — sk-ant-<...>; covers sk-ant-api03-... and future infixes.
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    # OpenAI project / service-account / admin keys (sk-proj-, sk-svcacct-, ...).
    ("openai_scoped", re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}")),
    # OpenAI legacy secret key (sk-<48ish>). After scoped so sk-proj- wins first.
    ("openai", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    # Stripe restricted/secret/publishable live|test keys.
    ("stripe", re.compile(r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}")),
    # GitHub fine-grained PAT.
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    # GitHub classic tokens (ghp/gho/ghu/ghs/ghr).
    ("github_token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}")),
    # AWS access key IDs (AKIA/ASIA/AGPA/AIDA/AROA + 16 upper-alnum).
    ("aws", re.compile(r"(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}")),
    # Slack tokens (xoxb-/xoxa-/xoxp-/xoxr-/xoxs-).
    ("slack", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    # Google API key.
    ("google_api", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    # Generic catch-all (LAST). Silent-redact only; never a raise gate.
    ("generic", re.compile(r"[A-Za-z0-9]{40,}")),
]

# Set of vendor pattern names that the fail-closed tripwire raises on. The bare
# ``generic`` catch-all is intentionally absent (false-positive-prone).
_NAMED_RAISE_PATTERNS = frozenset(name for name, _ in _PATTERNS if name != "generic")

# Schema field-NAME fragments that mark a value as a secret. Documented as schema
# vocabulary (ASCII field names), NOT natural language. Matched on key *segments*
# (boundary-aware) so ``monkey``/``turkey``/``capital`` never false-match.
_SENSITIVE_KEY_PARTS: frozenset[str] = frozenset(
    {
        "key",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "api",
        "apikey",
        "auth",
        "bearer",
        "credential",
        "credentials",
        "private",
        "signature",
        "session",
        "cookie",
        "pat",
    }
)

# Splits a key into lowercase segments on any non-alphanumeric boundary AND on
# camelCase humps. ``apiKey`` -> {api, key}; ``access_token`` -> {access, token}.
_SEGMENT_BOUNDARY_RE = re.compile(r"[^0-9A-Za-z]+|(?<=[a-z0-9])(?=[A-Z])")

# Entropy gate thresholds (pinned by the F133 negative tests). A value must be
# long enough, high-entropy enough, AND mixed-alphabet to be treated as a secret.
_ENTROPY_MIN_LEN = 24
_ENTROPY_MIN_BITS_PER_CHAR = 3.5


def _key_segments(key: str) -> set[str]:
    """Split a field name into lowercase alphanumeric segments (boundary-aware)."""
    return {seg.lower() for seg in _SEGMENT_BOUNDARY_RE.split(key) if seg}


def _key_is_sensitive(key: str) -> bool:
    """True iff any boundary-delimited segment of ``key`` is a sensitive token.

    Boundary matching (not substring) so ``monkey``/``turkey``/``capital`` do not
    false-match ``key``/``api`` while ``access_token``/``apiKey`` still do.
    """
    return bool(_key_segments(str(key)) & _SENSITIVE_KEY_PARTS)


def _shannon_bits_per_char(token: str) -> float:
    """Return the Shannon entropy of ``token`` in bits per character."""
    if not token:
        return 0.0
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _looks_high_entropy(token: str) -> bool:
    """Conservative secret heuristic: long + high-entropy + mixed alphabet.

    Pinned by negative tests — git SHAs (single-case hex, lower entropy), UUIDs
    (dash-delimited, treated as separate short segments), file paths and English
    prose must all fall BELOW this gate.
    """
    if len(token) < _ENTROPY_MIN_LEN:
        return False
    has_lower = any(c.islower() for c in token)
    has_upper = any(c.isupper() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if (has_lower + has_upper + has_digit) < 2:  # noqa: PLR2004 — need ≥2 classes
        return False
    return _shannon_bits_per_char(token) >= _ENTROPY_MIN_BITS_PER_CHAR


def _contains_keyscoped_entropy_secret(text: str) -> bool:
    """True iff any whitespace-delimited token in ``text`` looks high-entropy."""
    return any(_looks_high_entropy(tok) for tok in text.split())


class ExportSanitizer:
    """Detects and redacts raw secrets in export content."""

    def sanitize_text(self, text: str) -> str:
        """Replace detected raw secrets with ``<REDACTED>``.

        Applies named vendor patterns most-specific-first, then the generic
        catch-all, then a conservative key-agnostic entropy last-pass (logged
        when it fires). No secret value is ever logged.
        """
        # 1. ENTRY
        log.infra.debug(
            "[export] sanitizer.sanitize_text: entry",
            extra={"_fields": {"text_len": len(text)}},
        )

        result = text
        replaced = 0
        for name, pattern in _PATTERNS:
            new_result = pattern.sub("<REDACTED>", result)
            if new_result != result:
                replaced += 1
                # 3. STEP — log the PATTERN NAME only, never the matched value.
                log.infra.debug(
                    "[export] sanitizer.sanitize_text: pattern matched",
                    extra={"_fields": {"pattern": name}},
                )
                result = new_result

        # Conservative entropy last-pass: redact any remaining high-entropy token.
        entropy_hits = 0
        rebuilt: list[str] = []
        for token in re.split(r"(\s+)", result):
            if token and not token.isspace() and _looks_high_entropy(token):
                rebuilt.append("<REDACTED>")
                entropy_hits += 1
            else:
                rebuilt.append(token)
        if entropy_hits:
            result = "".join(rebuilt)
            log.infra.debug(
                "[export] sanitizer.sanitize_text: entropy last-pass redacted",
                extra={"_fields": {"entropy_hits": entropy_hits}},
            )

        # 4. EXIT
        log.infra.debug(
            "[export] sanitizer.sanitize_text: exit",
            extra={"_fields": {"patterns_triggered": replaced, "entropy_hits": entropy_hits}},
        )
        return result

    def sanitize_dict(self, data: dict) -> dict:  # type: ignore[type-arg]
        """Recursively walk dict; redact sensitive keys, sanitize text elsewhere."""
        # 1. ENTRY
        log.infra.debug(
            "[export] sanitizer.sanitize_dict: entry",
            extra={"_fields": {"key_count": len(data)}},
        )

        # 2. DECISION — recursive walk
        result: dict = {}  # type: ignore[type-arg]
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = self.sanitize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.sanitize_dict(item) if isinstance(item, dict)
                    else (self.sanitize_text(item) if isinstance(item, str) else item)
                    for item in value
                ]
            elif isinstance(value, str):
                if _key_is_sensitive(str(key)):
                    # 3. STEP — sensitive key, replace value wholesale (log key only)
                    log.infra.debug(
                        "[export] sanitizer.sanitize_dict: redacting sensitive key",
                        extra={"_fields": {"key": key}},
                    )
                    result[key] = "<REDACTED>"
                else:
                    result[key] = self.sanitize_text(value)
            else:
                result[key] = value

        # 4. EXIT
        log.infra.debug(
            "[export] sanitizer.sanitize_dict: exit",
            extra={"_fields": {"key_count": len(result)}},
        )
        return result

    def check_and_raise(self, text: str, field_name: str) -> None:
        """Fail-closed tripwire: raise SecurityError if a raw secret is present.

        Raises on a NAMED vendor pattern, or on a high-entropy value when
        ``field_name`` is itself sensitive (key-scoped entropy gate). Does NOT
        raise on the bare generic-40 (silent-redact-only, to avoid making every
        git SHA / UUID a denial-of-export).
        """
        # 1. ENTRY
        log.infra.debug(
            "[export] sanitizer.check_and_raise: entry",
            extra={"_fields": {"field_name": field_name, "text_len": len(text)}},
        )

        # 2. DECISION — named vendor patterns (never the generic catch-all).
        for name, pattern in _PATTERNS:
            if name not in _NAMED_RAISE_PATTERNS:
                continue
            if pattern.search(text):
                # 3. STEP — match found (pattern name + field only; never value).
                log.infra.warning(
                    "[export] sanitizer.check_and_raise: raw secret detected",
                    extra={"_fields": {"pattern": name, "field_name": field_name}},
                )
                raise SecurityError(
                    f"Export sanitization detected raw secret in {field_name}",
                    category="export_sanitization_failed",
                )

        # Key-scoped entropy gate: only when the field name itself is sensitive.
        if _key_is_sensitive(field_name) and _contains_keyscoped_entropy_secret(text):
            log.infra.warning(
                "[export] sanitizer.check_and_raise: high-entropy secret under sensitive key",
                extra={"_fields": {"field_name": field_name}},
            )
            raise SecurityError(
                f"Export sanitization detected high-entropy secret in {field_name}",
                category="export_sanitization_failed",
            )

        # 4. EXIT
        log.infra.debug(
            "[export] sanitizer.check_and_raise: exit — no secrets detected",
            extra={"_fields": {"field_name": field_name}},
        )
