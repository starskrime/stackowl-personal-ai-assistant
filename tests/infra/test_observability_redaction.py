"""FX-04 — SensitiveFieldFilter must redact secrets hiding inside a VALUE under
an innocuous key (e.g. a bearer token embedded in a logged shell command), not
just top-level keys matching a sensitive name pattern.
"""

from __future__ import annotations

import logging

from stackowl.infra.observability import SensitiveFieldFilter, _clean_value, _redact_string


def _filtered_fields(fields: dict) -> dict:
    record = logging.LogRecord(
        name="stackowl.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="test", args=None, exc_info=None,
    )
    record._fields = fields  # type: ignore[attr-defined]
    SensitiveFieldFilter().filter(record)
    return record._fields  # type: ignore[attr-defined,no-any-return]


def test_top_level_sensitive_key_still_redacted() -> None:
    """Pre-existing behavior must survive: a key matching a sensitive pattern
    is fully redacted regardless of its value's shape."""
    assert _clean_value("api_key", "anything-at-all") == "***"
    assert _clean_value("password", "hunnter2") == "***"


def test_url_query_string_still_stripped() -> None:
    """Pre-existing behavior: a bare URL value has its query string stripped."""
    out = _clean_value("url", "https://example.com/path?token=abc&x=1")
    assert out == "https://example.com/path"


def test_bearer_token_in_shell_command_value_is_redacted() -> None:
    """FX-04 — the real leak this fix targets: a rendered shell command logged
    under an innocuous key ("command") with an Authorization header inline."""
    fields = {
        "command": "curl -H 'Authorization: Bearer sk-abcdEFGH12345678901234' https://api.example.com",
    }
    out = _filtered_fields(fields)
    assert "sk-abcdEFGH12345678901234" not in out["command"]
    assert "Bearer ***" in out["command"] or "***" in out["command"]


def test_env_style_assignment_redacts_value_keeps_variable_name() -> None:
    out = _redact_string("export API_KEY=sk-liveSecretValue1234567890")
    assert "sk-liveSecretValue1234567890" not in out
    assert "API_KEY=***" in out


def test_aws_and_github_token_shapes_redacted() -> None:
    assert "AKIAABCDEFGHIJKLMNOP" not in _redact_string(
        "aws configure set aws_access_key_id AKIAABCDEFGHIJKLMNOP"
    )
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in _redact_string(
        "git clone https://ghp_abcdefghijklmnopqrstuvwxyz012345@github.com/x/y.git"
    )


def test_bearer_regex_does_not_over_redact_ordinary_english() -> None:
    """Regression: `bearer\\s+\\w+` with no length floor matched ordinary text
    like "the bearer of important news", corrupting non-secret log lines."""
    assert _redact_string("the bearer of important news") == "the bearer of important news"
    assert _redact_string("the bearer bond matured yesterday") == (
        "the bearer bond matured yesterday"
    )


def test_recurses_into_nested_dict_and_list_args() -> None:
    """FX-04 — nested structures (e.g. an argv list) must be scanned too, not
    just the top-level field value."""
    fields = {
        "args": ["curl", "-H", "Authorization: Bearer sk-nestedSecretToken1234567890"],
        "meta": {"note": "token=sk-anotherNestedSecret1234567890abc"},
    }
    out = _filtered_fields(fields)
    assert "sk-nestedSecretToken1234567890" not in str(out["args"])
    assert "sk-anotherNestedSecret1234567890abc" not in str(out["meta"])


def test_short_strings_are_not_scanned_for_performance() -> None:
    """Below the min-scan length, strings pass through untouched — this is a
    deliberate cost/coverage tradeoff, not a missed case (no real secret shape
    is this short)."""
    assert _redact_string("short") == "short"


def test_ordinary_non_secret_text_is_unchanged() -> None:
    out = _filtered_fields({"note": "the quick brown fox jumps over the lazy dog"})
    assert out["note"] == "the quick brown fox jumps over the lazy dog"
