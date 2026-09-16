"""Setup-code minting and consume-once semantics (AD-16, FR66)."""

from __future__ import annotations

from bridge_spike import setup_code as setup_code_module


def test_generate_setup_code_is_the_right_length_and_alphabet() -> None:
    code = setup_code_module.generate_setup_code()
    assert len(code) == setup_code_module.CODE_LENGTH
    assert all(char in setup_code_module._ALPHABET for char in code)
    assert "0" not in code and "O" not in code and "1" not in code and "I" not in code


def test_generate_setup_code_is_not_the_same_every_time() -> None:
    codes = {setup_code_module.generate_setup_code() for _ in range(50)}
    assert len(codes) > 1


def test_consume_succeeds_exactly_once_for_the_correct_code() -> None:
    store = setup_code_module.SetupCodeStore(code="ABCDEFGH")
    assert store.consume("ABCDEFGH") is True
    assert store.consumed is True
    # A second attempt with the SAME correct code must also fail --
    # consume-once, not "correct code always works".
    assert store.consume("ABCDEFGH") is False


def test_consume_fails_for_the_wrong_code_and_does_not_consume_it() -> None:
    store = setup_code_module.SetupCodeStore(code="ABCDEFGH")
    assert store.consume("WRONGONE") is False
    assert store.consumed is False
    # The real code still works afterwards.
    assert store.consume("ABCDEFGH") is True


def test_consume_rejects_non_string_input_without_raising() -> None:
    store = setup_code_module.SetupCodeStore(code="ABCDEFGH")
    assert store.consume(None) is False  # type: ignore[arg-type]
    assert store.consume(12345678) is False  # type: ignore[arg-type]
    assert store.consumed is False


def test_no_public_api_mints_a_new_code_after_construction() -> None:
    store = setup_code_module.SetupCodeStore(code="ABCDEFGH")
    original = store.code
    store.consume("ABCDEFGH")
    # The only way to get a fresh code is a new SetupCodeStore instance --
    # there is no "reset"/"reissue" method on the store itself.
    assert not hasattr(store, "reset")
    assert not hasattr(store, "reissue")
    assert store.code == original
