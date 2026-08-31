"""One incident skill per CAPABILITY, patched as new outcomes appear.

Bakir, 2026-08-30, choosing between four ways to constrain the miner: "merge into
one skill per tool". The class-level shape, and the opposite of what shipped.

WHY. Identity was `incident_<capability>_<failure>`, so the miner minted one skill
per (capability x failure) pair. MEASURED: 6 learned skills, ALL from that
template, 4 never executed; and the template's ceiling is 213 distinct tools x 11
failure classes = 2,343 possible skills. That is how the corpus reached 179 before
the operator purged it.

THE TRAP THIS MUST AVOID. Dropping failure_class from the identity without adding
a patch path would make the second outcome for a capability hit the existing
directory and be SKIPPED — silently losing every failure class after the first.
The old skip was correct for its old identity; under the new one it becomes data
loss.
"""

from __future__ import annotations

from stackowl.learning.failure_outcome_miner import (
    _canonical_incident_slug,
    _merge_outcome_into_body,
    _render_incident_body,
)


class _V:
    """A minimal RcaVerdict stand-in — only the fields the renderer reads."""

    def __init__(self, capability: str, failure: str, desc: str = "it broke") -> None:
        self.capability_class = capability
        self.failure_class = failure
        self.description = desc
        self.when_to_use = f"when {capability} hits {failure}"
        self.fix_pattern = f"retry {capability} after clearing state ({failure})"
        self.root_cause = f"{capability} left state behind on {failure}"
        self.skill_name = "proposed-name"
        self.parent_trace_ids = []


def test_identity_is_the_CAPABILITY_not_the_pair() -> None:
    """The whole change: two outcomes for one capability share a skill."""
    a = _canonical_incident_slug("shell", "stop")
    b = _canonical_incident_slug("shell", "unachieved_effect")
    assert a == b == "incident_shell"


def test_a_second_outcome_is_MERGED_not_skipped() -> None:
    body = _render_incident_body(_V("shell", "stop"))
    merged = _merge_outcome_into_body(body, _V("shell", "unachieved_effect"))

    assert merged is not None, "the second outcome was dropped"
    assert "stop" in merged and "unachieved_effect" in merged
    assert merged.count("## Quick Reference") == 1, "sections must not be duplicated"


def test_the_SAME_outcome_twice_is_a_no_op() -> None:
    """A still-open incident re-triggers a mining pass on every scheduler tick.
    Without idempotency the body would grow without bound — which is the same
    unbounded-growth defect one level down from the one being fixed."""
    body = _render_incident_body(_V("shell", "stop"))
    assert _merge_outcome_into_body(body, _V("shell", "stop")) is None


def test_the_merged_body_still_carries_every_required_section() -> None:
    """The renderer generates FROM the standard so a new section appears
    automatically. Merging must not break that contract — a body missing a
    required section is refused by validate_body and the write goes silent."""
    from stackowl.skills.standard import REQUIRED_SECTIONS

    body = _render_incident_body(_V("shell", "stop"))
    merged = _merge_outcome_into_body(body, _V("shell", "unachieved_effect"))
    assert merged is not None
    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in merged, f"merge lost the {section!r} section"


def test_a_THIRD_outcome_merges_too() -> None:
    body = _render_incident_body(_V("shell", "stop"))
    body = _merge_outcome_into_body(body, _V("shell", "unachieved_effect")) or body
    body = _merge_outcome_into_body(body, _V("shell", "timeout")) or body
    for f in ("stop", "unachieved_effect", "timeout"):
        assert f in body


def test_the_merged_body_VALIDATES() -> None:
    """The end-to-end contract: a merged body must pass the same validator the
    gated write applies, or the miner silently stops writing — which is exactly
    how ADR-19's learning half went quiet once before."""
    from stackowl.skills.standard import validate_body

    body = _render_incident_body(_V("shell", "stop"))
    merged = _merge_outcome_into_body(body, _V("shell", "unachieved_effect"))
    assert merged is not None
    assert validate_body(merged) == [] or all(
        not getattr(v, "blocking", True) for v in validate_body(merged)
    ), validate_body(merged)


def test_the_frontmatter_is_not_folded_into_the_body() -> None:
    """_render_skill_md puts the frontmatter back, so merging must strip it first.
    A doubled frontmatter block only shows up when the skill is next LOADED,
    which is exactly the kind of corruption that gets blamed on the loader."""
    from stackowl.learning.failure_outcome_miner import _body_of

    text = "---\nname: incident_shell\nversion: 0.1.0\n---\n\n## When to Use\n\nbody here\n"
    body = _body_of(text)
    assert body.startswith("## When to Use")
    assert "name: incident_shell" not in body


def test_a_body_with_NO_frontmatter_is_returned_unchanged() -> None:
    from stackowl.learning.failure_outcome_miner import _body_of

    assert _body_of("## When to Use\n\nx") == "## When to Use\n\nx"
