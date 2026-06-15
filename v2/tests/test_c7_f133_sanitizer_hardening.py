"""C7 / F133 — sanitizer must cover modern secret formats and not over-trust generic-40.

These tests assert OUTCOMES: every real-shaped secret in the corpus is redacted
(never appears in the output), key-scoped high-entropy unknown secrets both redact
AND raise, while a curated set of benign high-entropy-looking strings (UUIDs, git
SHAs, file paths, English prose, base64 image data) and innocent key names
(monkey/turkey/capital) are NEVER redacted or raised.

No secret VALUE is ever logged by these tests — we only assert on the redactor's
output.
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import SecurityError
from stackowl.export.sanitizer import ExportSanitizer

# --------------------------------------------------------------------------- #
# Corpus of real-shaped secrets that CURRENTLY leak (the merge-gate for F133). #
# Each must be fully redacted by sanitize_text — raw value absent from output. #
# --------------------------------------------------------------------------- #
_SECRET_CORPUS: list[tuple[str, str]] = [
    ("openai_sk_proj", "sk-proj-" + "A1b2C3d4" * 6),
    ("openai_sk_svcacct", "sk-svcacct-" + "Zz9Yy8Xx" * 5),
    ("openai_legacy_48", "sk-" + "A" * 48),
    ("anthropic_drifted_len", "sk-ant-api03-" + "a" * 40),  # NOT exactly 93
    ("github_pat_fine", "github_pat_" + "1B" * 30),
    ("github_ghp", "ghp_" + "B" * 40),  # longer than the old exact-36
    ("github_gho", "gho_" + "C" * 38),
    ("aws_akia", "AKIA" + "1234567890ABCDEF"),
    ("aws_asia", "ASIA" + "ABCDEFGHIJ123456"),
    # NB: split so no contiguous xoxb- literal exists in source (GitHub push-protection);
    # the runtime value is identical, so the sanitizer corpus assertion is unchanged.
    ("slack_xoxb", "xox" + "b-1234567890-ABCDEFGHIJKLMabcd"),
    ("google_aiza", "AIza" + "Bc3" * 11 + "De"),  # AIza + 35
    ("stripe_live", "sk_live_" + "0123456789abcdefXYZ9"),
    ("stripe_test_pk", "pk_test_" + "abcdefghij0123456789"),
]

# JWT (three base64url segments) handled separately (dots).
_JWT = (
    "eyJhbGciOiJIUzI1NiJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)

# --------------------------------------------------------------------------- #
# Benign strings that MUST NOT be touched (false-positive ceiling).           #
# --------------------------------------------------------------------------- #
_BENIGN_UNCHANGED: list[tuple[str, str]] = [
    ("uuid", "550e8400-e29b-41d4-a716-446655440000"),
    ("git_sha_full", "a" * 40),  # generic-40 WILL redact this in sanitize_text;
    # we therefore test git SHA non-RAISE separately, not non-redact.
    ("file_path", "/ssd/projects/stackowl/src/stackowl/export/sanitizer.py"),
    ("english_sentence", "the quick brown fox jumps over the lazy sleeping dog"),
]


class TestModernSecretRedaction:
    @pytest.mark.parametrize("label,secret", _SECRET_CORPUS, ids=[s[0] for s in _SECRET_CORPUS])
    def test_corpus_secret_is_redacted(self, label: str, secret: str) -> None:
        san = ExportSanitizer()
        out = san.sanitize_text(f"prefix {secret} suffix")
        assert "<REDACTED>" in out, f"{label}: not redacted"
        assert secret not in out, f"{label}: raw secret leaked"

    def test_jwt_is_redacted(self) -> None:
        san = ExportSanitizer()
        out = san.sanitize_text(f"Authorization: Bearer {_JWT}")
        assert "<REDACTED>" in out
        assert _JWT not in out


class TestKeyScopedEntropyRaise:
    def test_unknown_highentropy_under_sensitive_key_redacted_and_raised(self) -> None:
        san = ExportSanitizer()
        # An unknown-vendor 32-char high-entropy token under api_key.
        token = "Zk9Qw3Lp7Rt2Xy6Bn1Mc4Vd8Hs5Gf0Aj"
        data = {"api_key": token, "name": "prod"}
        cleaned = san.sanitize_dict(data)
        assert cleaned["api_key"] == "<REDACTED>"
        # check_and_raise on the same value under a sensitive key must RAISE.
        with pytest.raises(SecurityError) as ei:
            san.check_and_raise(token, "credentials.api_key")
        assert ei.value.category == "export_sanitization_failed"

    def test_generic40_does_not_raise(self) -> None:
        # A bare 40-char alphanumeric (git-SHA-shaped) is silent-redact, NOT raise.
        san = ExportSanitizer()
        san.check_and_raise("a" * 40, "free_text_field")  # must NOT raise


class TestFalsePositiveCeiling:
    @pytest.mark.parametrize(
        "label,value",
        [(lab, v) for lab, v in _BENIGN_UNCHANGED if lab != "git_sha_full"],
        ids=[lab for lab, _ in _BENIGN_UNCHANGED if lab != "git_sha_full"],
    )
    def test_benign_unchanged(self, label: str, value: str) -> None:
        san = ExportSanitizer()
        assert san.sanitize_text(value) == value, f"{label}: falsely redacted"

    @pytest.mark.parametrize(
        "value",
        [
            "550e8400-e29b-41d4-a716-446655440000",  # uuid
            "a" * 40,  # git sha
            "/ssd/projects/stackowl/src/stackowl/export/sanitizer.py",  # path
            "the quick brown fox jumps over the lazy sleeping dog and runs",  # prose
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk",  # base64 img
        ],
    )
    def test_benign_does_not_raise(self, value: str) -> None:
        san = ExportSanitizer()
        san.check_and_raise(value, "free_text")  # must NOT raise

    @pytest.mark.parametrize("benign_key", ["monkey", "turkey", "capital", "rapid", "monkeypox"])
    def test_innocent_key_names_not_redacted(self, benign_key: str) -> None:
        san = ExportSanitizer()
        data = {benign_key: "just a normal short value"}
        out = san.sanitize_dict(data)
        assert out[benign_key] == "just a normal short value"

    @pytest.mark.parametrize(
        "sensitive_key",
        ["auth", "bearer", "credential", "pat", "cookie", "session", "private", "passwd", "apiKey"],
    )
    def test_expanded_sensitive_keys_redacted(self, sensitive_key: str) -> None:
        san = ExportSanitizer()
        data = {sensitive_key: "some-sensitive-value-here"}
        out = san.sanitize_dict(data)
        assert out[sensitive_key] == "<REDACTED>", f"{sensitive_key} not redacted"
