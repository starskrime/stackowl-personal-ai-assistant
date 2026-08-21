"""The prompt's parts are named ONCE. Three hand-kept copies had already drifted.

D16.3's architect stage (designs/D16.3.md). `assemble.run()` composes SEVEN
contributors and then lists them again, twice:

    parts = [p for p in (base, capabilities, persona, owls_block,
                         skills_block, profile, state.stable_context) if p]
    audit_prompt_parts(session_key, {"base": ..., "capabilities": ..., ...})
    log.engine.info("[pipeline] assemble: exit", extra={"_fields": {
        "base_len": ..., "persona_len": ..., "owls_len": ..., ...}})

MEASURED ON LIVE DATA, not read off the source: across all 18 `assemble: exit` lines
the retained log holds, the `_len` fields are base, persona, banner, owls, skills,
profile, stable_context, memory, system. **`capabilities_len` is absent.** It is a
real part — 579 chars, measured in the code's own comment — whose size has never once
been recorded. Meanwhile `banner_len` and `memory_len` ARE logged and are NOT parts of
the composed prompt.

The comment directly above that log line says:

    "INFO, not DEBUG. These per-part sizes are the diagnostic D01.6 exists to obtain,
    and at debug level they vanished entirely: 0 of 17403 lines in the live log
    carried them, which is why prompt composition was unmeasurable."

D01.6 moved those sizes to INFO precisely so composition would be measurable. One part
is still unmeasurable, because a second hand-maintained list forgot it. That is
CLAUDE.md defect shape #3 — two copies of one rule — at three copies, inside the
function this item set out to abstract.

So the fix is not an abstraction for its own sake: it is making the list exist once,
and deriving the audit and the log from it so they cannot disagree again.
"""

from __future__ import annotations

from stackowl.pipeline.steps.assemble import PROMPT_PART_NAMES, compose_prompt_parts


class TestOneListDrivesEverything:
    def test_the_composed_order_is_explicit_and_stable(self) -> None:
        """ORDER IS BEHAVIOUR, not style. It is the cached prefix (Law 1): reordering
        changes prompt_hash and invalidates every live session's cache. Pinned as an
        equality so a reorder has to be deliberate."""
        assert PROMPT_PART_NAMES == (
            "base", "capabilities", "persona", "owls",
            "skills", "profile", "stable_context",
        )

    def test_every_part_reaches_the_audit_and_the_log(self) -> None:
        """The invariant the drift violated: what composes the prompt is exactly what
        gets audited and exactly what gets measured."""
        rendered = {name: f"<{name}>" for name in PROMPT_PART_NAMES}
        prompt, audit, fields = compose_prompt_parts(rendered)

        assert set(audit) == set(PROMPT_PART_NAMES)
        assert {f"{n}_len" for n in PROMPT_PART_NAMES} <= set(fields)

    def test_capabilities_is_measured_now(self) -> None:
        """The specific part that was missing, named so the regression is obvious."""
        _p, _a, fields = compose_prompt_parts(
            {n: "x" for n in PROMPT_PART_NAMES}
        )
        assert "capabilities_len" in fields

    def test_an_empty_part_is_dropped_from_the_prompt_but_still_reported(self) -> None:
        """A part that rendered to nothing must not add a blank stanza — but its zero
        still has to appear, because "this part was empty" is exactly the diagnostic
        D01.6 wanted and an absent field cannot say it."""
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["persona"] = "P"
        prompt, audit, fields = compose_prompt_parts(rendered)

        assert prompt == "P"
        assert fields["capabilities_len"] == 0
        assert audit["capabilities"] == ""

    def test_parts_are_joined_by_a_blank_line_in_order(self) -> None:
        """Byte-level composition, unchanged from the code this replaces."""
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["base"] = "A"
        rendered["persona"] = "B"
        rendered["profile"] = "C"
        prompt, _a, _f = compose_prompt_parts(rendered)

        assert prompt == "A\n\nB\n\nC"

    def test_all_empty_yields_no_prompt(self) -> None:
        """`system_prompt = "\\n\\n".join(parts) or None` — the existing contract, kept."""
        prompt, _a, _f = compose_prompt_parts({n: "" for n in PROMPT_PART_NAMES})
        assert prompt is None

    def test_an_unknown_part_cannot_sneak_in(self) -> None:
        """Composition is driven by the NAMED list, not by whatever the caller passed.
        A stray key must not silently enter the prompt — that would be a new way to
        invalidate every session's cache with no audit trail."""
        rendered = {n: "" for n in PROMPT_PART_NAMES}
        rendered["base"] = "A"
        rendered["surprise"] = "SHOULD NOT APPEAR"
        prompt, audit, _f = compose_prompt_parts(rendered)

        assert prompt == "A"
        assert "surprise" not in audit

    def test_a_missing_part_is_treated_as_empty(self) -> None:
        """Never KeyError on the prompt path. A caller that omits a part gets the same
        result as one that passes "" — prompt-building must not crash a turn."""
        prompt, audit, fields = compose_prompt_parts({"base": "A"})

        assert prompt == "A"
        assert audit["skills"] == ""
        assert fields["skills_len"] == 0


class TestTheCompositionIsByteIdentical:
    """INVARIANT I3, and the migration's whole safety story.

    A prompt that changes shape silently invalidates every live session's cached
    prefix (Law 1). So this pins the new composer against the LITERAL expression it
    replaced, rather than reasoning that they agree:

        parts = [p for p in (base, capabilities, persona, owls_block,
                             skills_block, profile, state.stable_context) if p]
        system_prompt = "\\n\\n".join(parts) or None
    """

    @staticmethod
    def _previous(base, capabilities, persona, owls, skills, profile, stable):
        parts = [
            p for p in (base, capabilities, persona, owls, skills, profile, stable)
            if p
        ]
        return "\n\n".join(parts) or None

    def test_it_matches_the_old_expression_across_every_empty_combination(self) -> None:
        """All 128 empty/non-empty combinations of seven parts. The interesting cases
        are the sparse ones — a blank part must not leave a double separator, which is
        exactly the kind of one-character drift that moves prompt_hash."""
        import itertools

        values = ["base", "caps", "persona", "owls", "skills", "profile", "stable"]
        for mask in itertools.product([True, False], repeat=7):
            args = [v if keep else "" for v, keep in zip(values, mask, strict=True)]
            expected = self._previous(*args)
            got, _a, _f = compose_prompt_parts(
                dict(zip(PROMPT_PART_NAMES, args, strict=True))
            )
            assert got == expected, f"drifted for mask {mask}: {got!r} != {expected!r}"

    def test_a_none_stable_context_behaves_as_before(self) -> None:
        """`state.stable_context` is `str | None`; the old tuple dropped None by
        falsiness. The new path coerces to "" — same result, asserted rather than
        assumed."""
        args = dict(zip(PROMPT_PART_NAMES, ["b", "", "", "", "", "", ""], strict=True))
        got, _a, _f = compose_prompt_parts(args)
        assert got == self._previous("b", "", "", "", "", "", None)
