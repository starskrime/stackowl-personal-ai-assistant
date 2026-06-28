"""GatewayScanner vocative-position display_name routing (ADR-D / S7).

Battery (the whole point: a bare owl name routes ONLY at a turn boundary):

  '<owl>, help me decide'   → owl route, stripped='help me decide'   (turn-initial)
  '<owl> help me'           → owl route, stripped='help me'          (initial, no comma)
  'remind me, <owl>'        → owl route, stripped='remind me'        (turn-terminal)
  'are you there, <owl>?'   → owl route                              (terminal + punct)
  'I saw <owl> yesterday'   → secretary  (mid-sentence — MUST NOT hijack)
  'I saw <owl>'             → secretary  (terminal WITHOUT a comma — not vocative)
  '@<owl> hi'               → owl route  (regression: @-prefix path untouched)
  'TONY, hi' vs stored Tony → owl route  (NFC + case-fold)
  'Help, …' vs 'I need help' → only the vocative one routes (name == common word)
  is_direct=False           → secretary  (multi-party group: no bare-name routing)
  ambiguous 2 close names   → secretary  (never guess)
  'Tóny' / 'Аудра'          → owl route  (diacritic + non-Latin, \\w/NFC/casefold)
"""

from __future__ import annotations

from stackowl.gateway.scanner import GatewayScanner, IngressMessage, RouteDecision
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry


def _owl(name: str, display: str = "") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        display_name=display,
        role="specialist",
        system_prompt="help",
        model_tier="standard",
    )


def _registry(*owls: OwlAgentManifest) -> OwlRegistry:
    reg = OwlRegistry()
    for o in owls:
        reg.register(o)
    return reg


def _scan(text: str, reg: OwlRegistry, *, is_direct: bool = True) -> RouteDecision:
    scanner = GatewayScanner(owl_registry=reg)
    return scanner.scan(
        IngressMessage(
            text=text,
            session_id="sess",
            channel="cli",
            trace_id="trace",
            is_direct=is_direct,
        )
    )


# --------------------------------------------------------------------------
# Turn-initial position
# --------------------------------------------------------------------------


def test_name_at_start_with_comma_routes() -> None:
    d = _scan("Tony, help me decide", _registry(_owl("tony", "Tony")))
    assert d.route == "owl"
    assert d.target == "tony"
    assert d.stripped_text == "help me decide"


def test_name_at_start_no_separator_routes() -> None:
    d = _scan("Tony help me", _registry(_owl("tony", "Tony")))
    assert d.target == "tony"
    assert d.stripped_text == "help me"


def test_name_at_start_with_colon_routes() -> None:
    d = _scan("Tony: status?", _registry(_owl("tony", "Tony")))
    assert d.target == "tony"
    assert d.stripped_text == "status?"


# --------------------------------------------------------------------------
# Turn-terminal position (requires a comma)
# --------------------------------------------------------------------------


def test_name_at_end_after_comma_routes() -> None:
    d = _scan("remind me, Tony", _registry(_owl("tony", "Tony")))
    assert d.target == "tony"
    assert d.stripped_text == "remind me"


def test_name_at_end_with_question_mark_routes() -> None:
    d = _scan("are you there, Tony?", _registry(_owl("tony", "Tony")))
    assert d.target == "tony"
    assert d.stripped_text == "are you there"


# --------------------------------------------------------------------------
# Anti-hijack: mid-sentence / non-vocative MUST fall through to secretary
# --------------------------------------------------------------------------


def test_name_mid_sentence_does_not_route() -> None:
    d = _scan("I saw Tony yesterday", _registry(_owl("tony", "Tony")))
    assert d.target == "secretary"


def test_name_at_end_without_comma_does_not_route() -> None:
    d = _scan("I really need to call Tony", _registry(_owl("tony", "Tony")))
    assert d.target == "secretary"


# --------------------------------------------------------------------------
# Regression: @Name path is untouched
# --------------------------------------------------------------------------


def test_at_prefix_still_routes() -> None:
    d = _scan("@tony hi", _registry(_owl("tony", "Tony")), is_direct=False)
    assert d.route == "owl"
    assert d.target == "tony"
    assert d.stripped_text == "hi"


# --------------------------------------------------------------------------
# Case-insensitive (NFC + case-fold), against name AND display_name
# --------------------------------------------------------------------------


def test_case_insensitive_uppercase_token_matches_stored_mixed_case() -> None:
    d = _scan("TONY, hello", _registry(_owl("tony", "Tony")))
    assert d.target == "tony"


def test_matches_display_name_when_slug_differs() -> None:
    # Spoken display "Tony" maps to the routing slug "t_stark".
    d = _scan("tony, suit up", _registry(_owl("t_stark", "Tony")))
    assert d.target == "t_stark"
    assert d.stripped_text == "suit up"


# --------------------------------------------------------------------------
# A name equal to a common word still ONLY routes in vocative position
# --------------------------------------------------------------------------


def test_common_word_name_routes_only_in_vocative_position() -> None:
    reg = _registry(_owl("help", "Help"))
    vocative = _scan("Help, what are my options", reg)
    assert vocative.target == "help"
    mid = _scan("I think I need help with this", reg)
    assert mid.target == "secretary"


# --------------------------------------------------------------------------
# Per-channel gate: multi-party channel does NOT NL-route a bare name
# --------------------------------------------------------------------------


def test_multi_party_channel_does_not_nl_route() -> None:
    d = _scan("Tony, help me", _registry(_owl("tony", "Tony")), is_direct=False)
    assert d.target == "secretary"


def test_at_prefix_still_works_in_multi_party() -> None:
    d = _scan("@tony help me", _registry(_owl("tony", "Tony")), is_direct=False)
    assert d.target == "tony"


# --------------------------------------------------------------------------
# Ambiguity: two close candidates → never guess, fall through to secretary
# --------------------------------------------------------------------------


def test_ambiguous_close_names_fall_through_to_secretary() -> None:
    reg = _registry(_owl("tony", "Tony"), _owl("tonya", "Tonya"))
    d = _scan("Tonye, hi", reg)  # within fuzzy threshold of BOTH
    assert d.target == "secretary"


def test_exact_match_still_wins_over_ambiguous_neighbour() -> None:
    # An exact token resolves even when a fuzzy neighbour exists.
    reg = _registry(_owl("tony", "Tony"), _owl("tonya", "Tonya"))
    d = _scan("Tony, hi", reg)
    assert d.target == "tony"


# --------------------------------------------------------------------------
# Unicode: diacritic + non-Latin names route (\\w / NFC / case-fold)
# --------------------------------------------------------------------------


def test_diacritic_name_routes() -> None:
    d = _scan("Tóny, hola", _registry(_owl("tony_es", "Tóny")))
    assert d.target == "tony_es"


def test_diacritic_name_routes_from_decomposed_input() -> None:
    # Combining acute (NFD: 'o' + U+0301) is NFC-normalised by the scanner
    # before matching the precomposed stored display.
    stored = "T\u00f3ny"            # precomposed (NFC)
    typed = "T" + "o\u0301" + "ny"  # decomposed (NFD) form of the same name
    d = _scan(typed + ", hola", _registry(_owl("tony_es", stored)))
    assert d.target == "tony_es"


def test_non_latin_name_routes() -> None:
    d = _scan("Аудра, привет", _registry(_owl("audra_ru", "Аудра")))
    assert d.target == "audra_ru"
    assert d.stripped_text == "привет"


# --------------------------------------------------------------------------
# Back-compat: no registry / no vocative match is byte-identical secretary
# --------------------------------------------------------------------------


def test_plain_text_without_owl_match_routes_secretary() -> None:
    d = _scan("what's the weather", _registry(_owl("tony", "Tony")))
    assert d.target == "secretary"
    assert d.stripped_text is None
