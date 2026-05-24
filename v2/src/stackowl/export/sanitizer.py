"""ExportSanitizer — scans export content for raw secrets and sensitive fields."""

from __future__ import annotations

import re

from stackowl.exceptions import SecurityError
from stackowl.infra.observability import log

# Ordered by specificity (most specific first to avoid false positives)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9\-]{93}")),
    ("openai", re.compile(r"sk-[A-Za-z0-9]{48}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("aws", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("generic", re.compile(r"[A-Za-z0-9]{40}")),
]

_SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password", "api")


def _key_is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(part in k for part in _SENSITIVE_KEY_PARTS)


class ExportSanitizer:
    """Detects and redacts raw secrets in export content."""

    def sanitize_text(self, text: str) -> str:
        """Replace detected raw secrets with <REDACTED>.

        Applies patterns in specificity order so longer/more-specific patterns
        win over the generic 40-char catch-all.
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
                log.infra.debug(
                    "[export] sanitizer.sanitize_text: pattern matched",
                    extra={"_fields": {"pattern": name}},
                )
                result = new_result

        # 4. EXIT
        log.infra.debug(
            "[export] sanitizer.sanitize_text: exit",
            extra={"_fields": {"patterns_triggered": replaced}},
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
                    # 3. STEP — sensitive key, replace value wholesale
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
        """Scan text; raise SecurityError if a raw secret pattern is found.

        Does NOT apply to the generic 40-char pattern to reduce false positives.
        """
        # 1. ENTRY
        log.infra.debug(
            "[export] sanitizer.check_and_raise: entry",
            extra={"_fields": {"field_name": field_name, "text_len": len(text)}},
        )

        # 2. DECISION — check named patterns only (skip generic)
        for name, pattern in _PATTERNS:
            if name == "generic":
                continue
            if pattern.search(text):
                # 3. STEP — match found
                log.infra.warning(
                    "[export] sanitizer.check_and_raise: raw secret detected",
                    extra={"_fields": {"pattern": name, "field_name": field_name}},
                )
                raise SecurityError(
                    f"Export sanitization detected raw secret in {field_name}",
                    category="export_sanitization_failed",
                )

        # 4. EXIT
        log.infra.debug(
            "[export] sanitizer.check_and_raise: exit — no secrets detected",
            extra={"_fields": {"field_name": field_name}},
        )
