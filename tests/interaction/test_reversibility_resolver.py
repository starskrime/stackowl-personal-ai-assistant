"""ADR-3 — ReversibilityResolver authority + the invariant it establishes.

The invariant (ADR-3): *a reversible, low-stakes decision is resolved with a stated
assumption + undo handle, never parked on the human; only irreversible-or-high-stakes
decisions reach the user.* These tests pin the authority's policy and prove the clarify
gate's de-facto ``_resolve_default`` rule delegates to it byte-identically.
"""

from __future__ import annotations

import pytest

from stackowl.interaction.reversibility_resolver import (
    Decision,
    Reversibility,
    ReversibilityResolver,
    Verdict,
)

# --------------------------------------------------------------- value semantics


def test_reversibility_tristate_constructors() -> None:
    assert Reversibility.reversible(via="undo_write").is_reversible
    assert Reversibility.reversible(via="undo_write").reversible_via == "undo_write"
    assert Reversibility.irreversible().is_irreversible
    assert Reversibility.unknown().is_unknown
    # the three states are mutually exclusive
    assert not Reversibility.irreversible().is_reversible
    assert not Reversibility.reversible().is_irreversible
    assert not Reversibility.unknown().is_reversible
    assert not Reversibility.unknown().is_irreversible


# --------------------------------------------------------------- the invariant


def test_reversible_low_stakes_with_clear_default_acts_and_retains_undo_handle() -> None:
    """The headline invariant: reversible + low-stakes + obvious default ⇒ ACT, state the
    assumption, KEEP the undo handle — never park."""
    resolver = ReversibilityResolver()
    verdict = resolver.resolve(
        Decision(
            reversibility=Reversibility.reversible(via="undo_write"),
            high_stakes=False,
            choices=("dark", "light"),
            default="dark",
        )
    )
    assert isinstance(verdict, Verdict)
    assert verdict.act is True
    assert verdict.assumption == "dark"
    assert verdict.undo_handle == "undo_write"


def test_one_item_menu_acts_on_the_sole_choice() -> None:
    resolver = ReversibilityResolver()
    verdict = resolver.resolve(
        Decision(reversibility=Reversibility.reversible(), choices=("only",))
    )
    assert verdict.act is True
    assert verdict.assumption == "only"


def test_irreversible_parks_on_the_human() -> None:
    """Only irreversible-or-high-stakes reach the user."""
    resolver = ReversibilityResolver()
    verdict = resolver.resolve(
        Decision(
            reversibility=Reversibility.irreversible(),
            choices=("send", "cancel"),
            default="send",
        )
    )
    assert verdict.act is False
    assert verdict.assumption is None
    assert verdict.undo_handle is None


def test_high_stakes_parks_even_when_reversible() -> None:
    resolver = ReversibilityResolver()
    verdict = resolver.resolve(
        Decision(
            reversibility=Reversibility.reversible(via="undo_write"),
            high_stakes=True,
            choices=("a",),
        )
    )
    assert verdict.act is False
    assert verdict.assumption is None


def test_genuine_multichoice_without_default_parks() -> None:
    """A real decision with no safe default still parks (byte-identical to today)."""
    resolver = ReversibilityResolver()
    verdict = resolver.resolve(
        Decision(reversibility=Reversibility.reversible(), choices=("approve", "reject"))
    )
    assert verdict.act is False
    assert verdict.assumption is None


def test_unknown_reversibility_with_default_acts_like_today() -> None:
    """UNKNOWN reversibility is treated exactly as the pre-ADR rule: a clear default still
    resolves (the old _resolve_default never had a reversibility signal at all)."""
    resolver = ReversibilityResolver()
    verdict = resolver.resolve(
        Decision(reversibility=Reversibility.unknown(), default="x", choices=("x", "y"))
    )
    assert verdict.act is True
    assert verdict.assumption == "x"


def test_default_inconsistent_with_menu_parks() -> None:
    resolver = ReversibilityResolver()
    verdict = resolver.resolve(
        Decision(
            reversibility=Reversibility.reversible(),
            default="z",
            choices=("x", "y"),
        )
    )
    assert verdict.act is False


# ------------------------------------- clarify gate delegates byte-identically


# (high_stakes, choices, default) → the pre-ADR _resolve_default expected return.
_RESOLVE_MATRIX = [
    (True, ("a",), None, None),  # high stakes always parks
    (True, (), "a", None),
    (False, ("only",), None, "only"),  # one-item menu
    (False, ("a", "b"), "a", "a"),  # default consistent with menu
    (False, ("a", "b"), "z", None),  # default not in menu
    (False, ("a", "b"), None, None),  # genuine multi-choice, no default
    (False, (), "d", "d"),  # default, no menu
    (False, (), None, None),  # nothing to go on
]


@pytest.mark.parametrize("high_stakes,choices,default,expected", _RESOLVE_MATRIX)
def test_clarify_resolve_default_off_is_pre_adr(
    monkeypatch: pytest.MonkeyPatch,
    high_stakes: bool,
    choices: tuple[str, ...],
    default: str | None,
    expected: str | None,
) -> None:
    """Flag OFF ⇒ ClarifyGateway._resolve_default returns the exact pre-ADR value."""
    from stackowl.interaction import clarify_gateway

    monkeypatch.setattr(
        clarify_gateway, "reversibility_resolver_enabled", lambda: False
    )
    got = clarify_gateway.ClarifyGateway._resolve_default(
        choices=choices, default=default, high_stakes=high_stakes
    )
    assert got == expected


@pytest.mark.parametrize("high_stakes,choices,default,expected", _RESOLVE_MATRIX)
def test_clarify_resolve_default_on_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
    high_stakes: bool,
    choices: tuple[str, ...],
    default: str | None,
    expected: str | None,
) -> None:
    """Flag ON ⇒ the same _resolve_default delegates to the resolver and returns the SAME
    value for every input (the resolver is a strict generalization of the inline rule)."""
    from stackowl.interaction import clarify_gateway

    monkeypatch.setattr(
        clarify_gateway, "reversibility_resolver_enabled", lambda: True
    )
    got = clarify_gateway.ClarifyGateway._resolve_default(
        choices=choices, default=default, high_stakes=high_stakes
    )
    assert got == expected


def test_clarify_resolve_default_on_consults_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the authority is actually consulted when the flag is ON (not just that the
    answer happens to match)."""
    from stackowl.interaction import clarify_gateway, reversibility_resolver

    calls: list[Decision] = []
    real_resolve = ReversibilityResolver.resolve

    def spy(self: ReversibilityResolver, decision: Decision) -> Verdict:
        calls.append(decision)
        return real_resolve(self, decision)

    monkeypatch.setattr(
        clarify_gateway, "reversibility_resolver_enabled", lambda: True
    )
    monkeypatch.setattr(reversibility_resolver.ReversibilityResolver, "resolve", spy)

    clarify_gateway.ClarifyGateway._resolve_default(
        choices=("only",), default=None, high_stakes=False
    )
    assert len(calls) == 1
    assert calls[0].choices == ("only",)


def test_clarify_resolve_default_off_does_not_consult_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.interaction import clarify_gateway, reversibility_resolver

    called = False

    def spy(self: ReversibilityResolver, decision: Decision) -> Verdict:
        nonlocal called
        called = True
        return Verdict(act=False)

    monkeypatch.setattr(
        clarify_gateway, "reversibility_resolver_enabled", lambda: False
    )
    monkeypatch.setattr(reversibility_resolver.ReversibilityResolver, "resolve", spy)

    clarify_gateway.ClarifyGateway._resolve_default(
        choices=("only",), default=None, high_stakes=False
    )
    assert called is False


# --------------------------------------------- must_reach_user (objective driver gate)


def test_must_reach_user_irreversible_and_high_stakes() -> None:
    assert ReversibilityResolver.must_reach_user(
        Decision(reversibility=Reversibility.irreversible())
    )
    assert ReversibilityResolver.must_reach_user(
        Decision(reversibility=Reversibility.reversible(), high_stakes=True)
    )


def test_must_reach_user_reversible_does_not_escalate() -> None:
    assert not ReversibilityResolver.must_reach_user(
        Decision(reversibility=Reversibility.reversible())
    )


@pytest.mark.parametrize("consequential", [(), ("send_email",)])
def test_objective_park_classification_byte_identical_off(
    monkeypatch: pytest.MonkeyPatch, consequential: tuple[str, ...]
) -> None:
    """Flag OFF ⇒ _park_is_irreversible == bool(consequential_failures), unchanged."""
    from types import SimpleNamespace

    from stackowl.objectives import driver as driver_mod

    monkeypatch.setattr(driver_mod, "reversibility_resolver_enabled", lambda: False)
    state = SimpleNamespace(consequential_failures=consequential)
    got = driver_mod.ObjectiveDriverHandler._park_is_irreversible(state)  # type: ignore[arg-type]
    assert got is bool(consequential)


@pytest.mark.parametrize("consequential", [(), ("send_email",)])
def test_objective_park_classification_byte_identical_on(
    monkeypatch: pytest.MonkeyPatch, consequential: tuple[str, ...]
) -> None:
    """Flag ON ⇒ same classification, now via the resolver authority."""
    from types import SimpleNamespace

    from stackowl.objectives import driver as driver_mod

    monkeypatch.setattr(driver_mod, "reversibility_resolver_enabled", lambda: True)
    state = SimpleNamespace(consequential_failures=consequential)
    got = driver_mod.ObjectiveDriverHandler._park_is_irreversible(state)  # type: ignore[arg-type]
    assert got == bool(consequential)


def test_objective_park_on_consults_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from stackowl.interaction import reversibility_resolver
    from stackowl.objectives import driver as driver_mod

    calls: list[Decision] = []
    real = ReversibilityResolver.must_reach_user

    def spy(decision: Decision) -> bool:
        calls.append(decision)
        return real(decision)

    monkeypatch.setattr(driver_mod, "reversibility_resolver_enabled", lambda: True)
    monkeypatch.setattr(
        reversibility_resolver.ReversibilityResolver, "must_reach_user", staticmethod(spy)
    )

    driver_mod.ObjectiveDriverHandler._park_is_irreversible(
        SimpleNamespace(consequential_failures=("send_email",))  # type: ignore[arg-type]
    )
    assert len(calls) == 1
    assert calls[0].reversibility.is_irreversible
