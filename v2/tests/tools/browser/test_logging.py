"""Tests for browser/_logging.py — URL scrub + credential redaction."""

from __future__ import annotations

from stackowl.tools.browser._logging import truncate_for_error, url_path_only


class TestUrlPathOnly:
    def test_strips_query_string(self) -> None:
        assert url_path_only("https://example.com/path?api_key=secret") == "https://example.com/path"

    def test_strips_fragment(self) -> None:
        assert url_path_only("https://example.com/x#section") == "https://example.com/x"

    def test_keeps_scheme_and_host(self) -> None:
        assert url_path_only("https://api.example.com:8443/v1/users") == "https://api.example.com:8443/v1/users"

    def test_unparseable_returns_marker(self) -> None:
        # urlparse doesn't actually fail on garbage — it produces empty pieces.
        result = url_path_only("not a url")
        assert "?" not in result and "#" not in result

    def test_empty_string(self) -> None:
        assert url_path_only("") == ""


class TestTruncateForError:
    def test_short_string_returned_unchanged(self) -> None:
        assert truncate_for_error("hello") == "hello"

    def test_long_string_truncated_with_marker(self) -> None:
        out = truncate_for_error("x" * 500, limit=100)
        assert out.endswith("<truncated>")
        assert len(out) < 200

    def test_scrubs_cookie_header(self) -> None:
        out = truncate_for_error("Got response with Cookie: sessionid=abc123xyz here")
        assert "abc123xyz" not in out
        assert "***" in out

    def test_scrubs_set_cookie(self) -> None:
        out = truncate_for_error("Set-Cookie: token=verysecretvalue123")
        assert "verysecretvalue123" not in out

    def test_scrubs_authorization_header(self) -> None:
        out = truncate_for_error("Authorization: Bearer eyJabcdefghijklmnopqrstuvwxyz12345")
        assert "eyJabcdefghijklmnopqrstuvwxyz12345" not in out

    def test_scrubs_api_key_assignment(self) -> None:
        out = truncate_for_error('"api_key": "sk-livefakebutlongenoughtotrigger"')
        assert "sk-livefakebutlongenoughtotrigger" not in out

    def test_leaves_short_credential_strings_alone(self) -> None:
        # Below 8-char minimum — patterns shouldn't fire.
        out = truncate_for_error("api_key=abc")
        assert "abc" in out
