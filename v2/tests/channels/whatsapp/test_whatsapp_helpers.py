"""Tests for WhatsApp channel adapter helpers — Story 9.8."""

from __future__ import annotations

import pytest

from stackowl.channels.whatsapp.helpers import (
    WhatsAppMarkdownFormatter,
    hash_jid,
    is_authorized,
    normalize_phone,
)


# --------------------------------------------------------------------------- #
# hash_jid
# --------------------------------------------------------------------------- #


def test_hash_jid_produces_8_char_hex() -> None:
    """hash_jid returns exactly 8 lowercase hex characters."""
    digest = hash_jid("15551234567@s.whatsapp.net")
    assert len(digest) == 8
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_jid_different_jids_different_hashes() -> None:
    """Different JIDs produce different hashes (no collision for typical inputs)."""
    h1 = hash_jid("15551234567@s.whatsapp.net")
    h2 = hash_jid("15559876543@s.whatsapp.net")
    assert h1 != h2


def test_hash_jid_stable() -> None:
    """Same JID always produces the same hash."""
    jid = "15551234567@s.whatsapp.net"
    assert hash_jid(jid) == hash_jid(jid)


# --------------------------------------------------------------------------- #
# normalize_phone
# --------------------------------------------------------------------------- #


def test_normalize_phone_strips_non_digits() -> None:
    """normalize_phone strips '+', spaces, dashes from phone strings."""
    assert normalize_phone("+1 (555) 123-4567") == "15551234567"


def test_normalize_phone_already_digits() -> None:
    """normalize_phone is idempotent on digit-only strings."""
    assert normalize_phone("15551234567") == "15551234567"


# --------------------------------------------------------------------------- #
# is_authorized
# --------------------------------------------------------------------------- #


def test_is_authorized_empty_set_deny_all() -> None:
    """Empty frozenset means fail-closed — no sender is authorized."""
    jid = "15551234567@s.whatsapp.net"
    assert is_authorized(jid, frozenset()) is False


def test_is_authorized_matching_number_allows() -> None:
    """JID whose phone matches an allowed number is authorized."""
    jid = "15551234567@s.whatsapp.net"
    assert is_authorized(jid, frozenset(["15551234567"])) is True


def test_is_authorized_non_matching_number_denies() -> None:
    """JID whose phone is not in the allow-list is denied."""
    jid = "15559999999@s.whatsapp.net"
    assert is_authorized(jid, frozenset(["15551234567"])) is False


def test_is_authorized_e164_with_plus_matches() -> None:
    """E.164 format with leading + in allowed set matches the JID's digits."""
    jid = "15551234567@s.whatsapp.net"
    assert is_authorized(jid, frozenset(["+15551234567"])) is True


def test_is_authorized_malformed_jid_denies() -> None:
    """A JID without the expected @ separator is silently denied."""
    assert is_authorized("not-a-jid", frozenset(["15551234567"])) is False


# --------------------------------------------------------------------------- #
# WhatsAppMarkdownFormatter
# --------------------------------------------------------------------------- #


def test_format_response_non_empty_output() -> None:
    """format_response returns a non-empty string for non-empty input."""
    formatter = WhatsAppMarkdownFormatter()
    result = formatter.format_response("Hello, world!")
    assert len(result) > 0
    assert "Hello" in result


def test_format_response_passthrough() -> None:
    """format_response does not corrupt input text."""
    formatter = WhatsAppMarkdownFormatter()
    text = "This is *bold* and _italic_ text."
    assert formatter.format_response(text) == text


def test_format_morning_brief_sections_appear_in_output() -> None:
    """format_morning_brief includes all section titles and bodies."""
    formatter = WhatsAppMarkdownFormatter()
    sections = {"Priorities": "Ship the feature", "Calendar": "3pm standup"}
    result = formatter.format_morning_brief(sections)
    assert "Priorities" in result
    assert "Ship the feature" in result
    assert "Calendar" in result
    assert "3pm standup" in result


def test_format_morning_brief_titles_are_bold() -> None:
    """format_morning_brief wraps section titles with WhatsApp bold markers."""
    formatter = WhatsAppMarkdownFormatter()
    sections = {"Section": "body"}
    result = formatter.format_morning_brief(sections)
    assert "*Section*" in result


def test_format_parliament_synthesis_owl_names_appear() -> None:
    """format_parliament_synthesis includes all participating owl names."""
    formatter = WhatsAppMarkdownFormatter()
    owl_names = ["Architect", "Critic", "Synthesizer"]
    result = formatter.format_parliament_synthesis("The answer is 42.", owl_names)
    for name in owl_names:
        assert name in result


def test_format_parliament_synthesis_contains_synthesis_text() -> None:
    """format_parliament_synthesis includes the synthesis content."""
    formatter = WhatsAppMarkdownFormatter()
    synthesis = "Consensus reached: deploy on Friday."
    result = formatter.format_parliament_synthesis(synthesis, ["Owl1"])
    assert synthesis in result
