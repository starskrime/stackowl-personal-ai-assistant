"""The per-turn INFO line must carry the lane, or lane depth is unmeasurable.

WHY THIS EXISTS, and it cost four wrong turns in one diagnosis to find.

D09.4's validate has sat `partial` since 2026-09-02 with the closing check "the skills
nudge fired", interval 10. Answering the obvious question — *has any lane ever reached
ten turns?* — should be one query against `[pipeline] execute: turn context composed`,
which is INFO and fires on every single turn.

It cannot be. That line carries `trace_id, channel, shaped, nudged` and **no
`session_key`**. A trace id is unique per TURN, so grouping by it makes every lane look
exactly one turn deep. Measured 2026-09-06: that produced "68 lanes, max depth 4, zero
lanes reached 10", which read as a settled answer and was an artefact of the missing
field.

THE REAL NUMBER, recovered from the nudge's OWN log line (which does carry
`session_key`): 47 lanes have fired the memory nudge, the deepest 16 times — the memory
nudge fires every 4 turns and resets, so that lane reached roughly **64** turns, and
**nine** lanes implied 10 or more. The two readings disagree by more than an order of
magnitude, and the wrong one came from the line designed for exactly this question.

That is CLAUDE.md's instrument rule in its purest form: *print the real shape before you
filter it*. A field that is absent does not announce itself — `f.get("session_key")`
returns None and the fallback quietly answers a different question. The fix is not a
better query, it is the missing field.

AND `nudged` MEANT ONLY HALF OF WHAT IT SAYS. It was `bool(nudge)` — the MEMORY nudge
alone — while the skill nudge composed onto the same context two statements later was
invisible. One name covering two mechanisms, reporting one: the shape this codebase
keeps paying for, inside the line whose whole job is to report what the turn was told.
"""

from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_EXECUTE = _ROOT / "src" / "stackowl" / "pipeline" / "steps" / "execute.py"


def _turn_line_fields() -> set[str]:
    """The `_fields` keys on the turn-context-composed INFO call, read from the AST.

    Parsed rather than grepped: a substring search would match the same words in the
    surrounding comments, which is how three guards in this repo were satisfied by
    prose describing the rule instead of the code obeying it.
    """
    tree = ast.parse(_EXECUTE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != "[pipeline] execute: turn context composed":
            continue
        for kw in node.keywords:
            if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                continue
            for k, v in zip(kw.value.keys, kw.value.values, strict=False):
                if isinstance(k, ast.Constant) and k.value == "_fields":
                    assert isinstance(v, ast.Dict)
                    return {
                        c.value for c in v.keys
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    }
    raise AssertionError("the turn-context-composed INFO call was not found")


def _turn_line_mapping() -> dict[str, str]:
    """Each `_fields` key mapped to the source text of its VALUE.

    Names alone are not enough: `"nudged": bool(skill_nudge)` satisfies every
    name-based assertion while reporting the wrong mechanism under each label, which
    is the exact confusion this line already shipped once.
    """
    tree = ast.parse(_EXECUTE.read_text(encoding="utf-8"))
    src = _EXECUTE.read_text(encoding="utf-8")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        a0 = node.args[0]
        if not isinstance(a0, ast.Constant) or a0.value != (
            "[pipeline] execute: turn context composed"
        ):
            continue
        for kw in node.keywords:
            if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                continue
            for k, v in zip(kw.value.keys, kw.value.values, strict=False):
                if isinstance(k, ast.Constant) and k.value == "_fields":
                    assert isinstance(v, ast.Dict)
                    return {
                        kk.value: ast.get_source_segment(src, vv) or ""
                        for kk, vv in zip(v.keys, v.values, strict=False)
                        if isinstance(kk, ast.Constant)
                    }
    raise AssertionError("the turn-context-composed INFO call was not found")


class TestTheLineCanAnswerItsOwnQuestion:
    def test_it_carries_the_lane(self) -> None:
        """WITHOUT THIS, DEPTH IS UNMEASURABLE. `trace_id` is unique per turn, so a
        reader grouping by it gets "every lane is one turn deep" — a confident wrong
        answer, which is worse than no answer."""
        assert "session_key" in _turn_line_fields(), (
            "the per-turn line has no session_key, so no query over it can say how "
            "many turns a lane ran — the question every nudge interval depends on"
        )

    def test_it_still_carries_what_it_already_answered(self) -> None:
        """Scope control. This line closes D08.1's shaped-channel check and the
        memory-nudge check; adding a field must not quietly drop one."""
        fields = _turn_line_fields()

        assert {"trace_id", "channel", "shaped"} <= fields

    def test_the_two_nudges_are_reported_SEPARATELY(self) -> None:
        """`nudged` was `bool(nudge)` — the memory nudge only — while the skill nudge
        rode the same context and appeared nowhere. Two mechanisms behind one name,
        reporting one of them, is indistinguishable from the other never running."""
        fields = _turn_line_fields()

        assert "nudged" in fields
        assert "skill_nudged" in fields, (
            "the skill nudge is composed onto this turn's context and is not reported, "
            "so its delivery cannot be counted from the logs at all"
        )


    def test_each_label_reports_its_OWN_mechanism(self) -> None:
        """The names being present proves nothing if the values are crossed. This is
        the vacuity control: `"nudged": bool(skill_nudge)` would satisfy every
        assertion above while reporting the wrong thing under both labels."""
        mapping = _turn_line_mapping()

        assert "skill" not in mapping["nudged"], (
            f"`nudged` reports {mapping['nudged']} — that is not the memory nudge"
        )
        assert "skill_nudge" in mapping["skill_nudged"], (
            f"`skill_nudged` reports {mapping['skill_nudged']}"
        )
        assert mapping["session_key"] == "state.session_key"
