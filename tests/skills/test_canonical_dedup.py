"""ESC-52 — the same name rearranged is the same skill, and reinforcing it must
not invent a use.

MEASURED 2026-08-24 across 171 non-archived skills: 168 near-duplicate pairs
covering 102 of them, and SIX families sharing an identical token set. Three of
the six differ only by SEPARATOR — invisible to `base_name`, which strips a `-N`
suffix and nothing else.

The last of those six was minted by the synthesizer at 08:33 that morning:
`incident_evidence_brief` beside the existing `incident-evidence-brief`, hours
after the fix that revived autonomous authoring. Restoring the loop restored
duplicate production, because the gate could not see the collision.

The fixtures below are the REAL six families, read from the live corpus. A test
that invented its own pairs would prove the function works on the cases I thought
of, which is exactly the blind spot that produced this.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from stackowl.skills.standard import base_name, canonical_key
from stackowl.skills.synthesizer import SkillSynthesizer


def _calls_in(fn) -> set[str]:  # type: ignore[no-untyped-def]
    """Names actually CALLED in `fn` — comments and strings excluded.

    Covers both `self.foo()` (Attribute) and bare `foo()` (Name), because the two
    canonical keys are reached one of each way: `set_lifecycle_state` through the
    store, `_canonical_key` as a module-level alias.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            out.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            out.add(node.func.id)
    return out

#: Verbatim from the live `skills` table, 2026-08-24.
REAL_FAMILIES = [
    ("download-instagram-video", "instagram-video-download"),
    ("incident-evidence-brief", "incident_evidence_brief"),
    ("check-stock-price-today", "check_stock_price_today"),
    ("memory_unachieved_effect_fallback", "unachieved_effect_memory_fallback"),
    ("report-task-status", "task-status-report"),
    ("check-stock-price-alert", "stock-price-alert-check"),
]


# ---------------------------------------------------------------------------
# The six families that actually exist
# ---------------------------------------------------------------------------

def test_every_real_family_collapses_to_one_key() -> None:
    for a, b in REAL_FAMILIES:
        assert canonical_key(a) == canonical_key(b), (a, b)


def test_base_name_could_NOT_see_them() -> None:
    """The reason this function exists. If base_name caught these, it would be
    two copies of one rule rather than two rules that compose."""
    missed = [(a, b) for a, b in REAL_FAMILIES if base_name(a) != base_name(b)]
    assert len(missed) == len(REAL_FAMILIES), (
        "base_name is expected to miss ALL of these — it strips `-N` and nothing "
        f"else. Caught unexpectedly: {set(REAL_FAMILIES) - set(missed)}"
    )


def test_the_separator_variants_specifically() -> None:
    """Three of the six differ ONLY by hyphen-vs-underscore. This is the case a
    `\\w`-based tokenizer cannot see, because `\\w` includes the underscore — the
    mistake that made my own duplicate measurement read 35% instead of 60%."""
    assert canonical_key("incident-evidence-brief") == canonical_key(
        "incident_evidence_brief"
    )
    assert canonical_key("check-stock-price-today") == canonical_key(
        "check_stock_price_today"
    )


# ---------------------------------------------------------------------------
# It composes with base_name, and it is not fuzzy
# ---------------------------------------------------------------------------

def test_it_composes_with_the_numbered_suffix_rule() -> None:
    """`foo-2` must still collapse onto `foo` — canonical_key applies base_name
    first, so the two rules compose instead of competing."""
    assert canonical_key("foo-2") == canonical_key("foo")
    assert canonical_key("deploy-app-3") == canonical_key("app-deploy")


def test_it_is_NOT_fuzzy() -> None:
    """A wrong merge corrupts a reader; a duplicate only wastes a row. The
    asymmetry is why near-misses must not match."""
    for a, b in [
        ("scout", "scouts"),
        ("deploy", "deploys"),
        ("check-stock-price", "check-stock-prices"),
        ("web-fetch", "web-fetcher"),
    ]:
        assert canonical_key(a) != canonical_key(b), (a, b)


def test_a_genuinely_different_skill_keeps_its_own_key() -> None:
    assert canonical_key("download-instagram-video") != canonical_key(
        "download-youtube-video"
    )


def test_empty_and_odd_input_do_not_raise() -> None:
    for odd in ("", "   ", "---", "___", "42", "a"):
        assert isinstance(canonical_key(odd), str)


# ---------------------------------------------------------------------------
# The reinforce must not fake a use
# ---------------------------------------------------------------------------

def test_reinforcement_does_not_claim_an_execution() -> None:
    """It called increment_n_executions, which bumps n_executions AND stamps
    last_used_at AND flips lifecycle to active — three effects where only the
    third was wanted.

    This has a measurable cost now, not just a principled one: ESC-44's catalogue
    ordering sorts by n_executions, so a faked use would push a skill up the list
    the model actually sees. Reinforcement would be buying visibility with
    invented evidence.
    """
    # Inspect the CALLS, not the source text. My first version grepped for the
    # string and failed on its own explanatory comment — the same over-literal
    # mistake as pinning a guard's spelling instead of its behaviour.
    called = _calls_in(SkillSynthesizer._reinforce_if_known)
    assert "increment_n_executions" not in called, (
        "reinforcement must revive the skill without claiming a use"
    )
    assert "set_lifecycle_state" in called, "revival is still the intended effect"


def test_the_re_derivation_is_recorded_as_provenance() -> None:
    """Not counted as usage — audited. `op` is free-form and the table has no
    CHECK constraint, so 'reinforce' sits beside create/edit/rename honestly."""
    src = inspect.getsource(SkillSynthesizer._reinforce_if_known)
    assert 'op="reinforce"' in src


def test_both_keys_are_consulted() -> None:
    """Assert the CALLS, not the source text — same reason as above."""
    called = _calls_in(SkillSynthesizer._reinforce_if_known)
    assert {"_base_name", "_canonical_key"} <= called, (
        "base_name catches `-N` variants, canonical_key catches rearrangements; "
        f"dropping either loses a class of duplicate. Called: {sorted(called)}"
    )
