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


# --------------------------------------------------------------------------
# D01.7 — `*_key` was masking identifiers, not just credentials.
#
# Found by the live validation run: `session.resolve: branch taken` logged
# session_key as "***", which made every jq query in designs/D01.7.md useless.
# The rule was over-broad for every *_key identifier in the tree, not only the
# one this item added.
# --------------------------------------------------------------------------


def test_identifier_keys_are_not_redacted() -> None:
    from stackowl.infra.observability import _is_sensitive

    for name in ("session_key", "resume_session_key", "identity_key", "owner_key",
                 "scope_key", "idempotency_key", "occurrence_key", "delegate_key",
                 "channel_key", "stream_key", "request_key"):
        assert not _is_sensitive(name), f"{name} is an identifier, not a credential"


def test_real_credentials_ending_in_key_are_still_redacted() -> None:
    """The allowlist must not have widened the hole it was narrowing."""
    from stackowl.infra.observability import _is_sensitive

    for name in ("api_key", "private_key", "embedded_private_key", "secret_key",
                 "signing_key", "encryption_key"):
        assert _is_sensitive(name), f"{name} must stay redacted"


def test_an_unknown_key_name_still_defaults_to_redacted() -> None:
    """Fail-closed: forgetting to think about a new *_key name must mean 'too
    private', never 'leaked a credential'."""
    from stackowl.infra.observability import _is_sensitive

    assert _is_sensitive("some_new_key")
    assert _is_sensitive("customer_api_key")


def test_the_lane_survives_the_real_logging_pipeline() -> None:
    """Filter THEN formatter — the actual order a record travels.

    Redaction lives in SensitiveFieldFilter, not the formatter; a test that calls
    the formatter alone proves nothing about redaction in either direction.
    """
    import json
    import logging

    from stackowl.infra.observability import JsonlFormatter, SensitiveFieldFilter

    record = logging.LogRecord(
        name="stackowl.gateway", level=logging.INFO, pathname=__file__, lineno=1,
        msg="session.resolve: branch taken", args=(), exc_info=None,
    )
    record._fields = {"session_key": "owl:secretary:telegram:dm:72055773",  # type: ignore[attr-defined]
                      "api_key": "sk-abcdef0123456789abcdef"}
    SensitiveFieldFilter().filter(record)
    out = json.loads(JsonlFormatter().format(record))
    assert out["fields"]["session_key"] == "owl:secretary:telegram:dm:72055773"
    assert out["fields"]["api_key"] == "***"
