"""A log line that says something was LOST must say WHICH thing.

MEASURED 2026-09-08 by walking every `log.<name>.{warning,error,critical,
exception}` call in `src/stackowl/` and reading its message literal: **41 claim a
loss** — dropped, discarded, not delivered, lost — and **7 carried no `extra=`
payload at all.** Four of the seven are the same sentence written independently in
three channel adapters:

    [discord]  adapter.send_text: no target channel (best-effort) — message dropped
    [discord]  adapter.send_file: no target channel (best-effort) — file dropped
    [whatsapp] adapter.send_text: no target chat (best-effort) — message dropped
    [whatsapp] adapter.send_file: no target chat (best-effort) — file dropped

THE LIVE COST, on the fourth adapter, which DID carry a payload: telegram's
`adapter.send_text: no active chat (best-effort) — message dropped` fired **61
times** in the retained window and every single record reads `{"has_app": true}`
— one constant, identical across all 61. A user-facing message was thrown away
sixty-one times and the record cannot say how long it was, which call site
produced it, or whether the sixty-one were one incident or sixty-one.

SO "CARRIES AN EXTRA" IS NOT "CAN BE INVESTIGATED", and the denominator says so:
34 of 41 carry a payload, which looks healthy and was not. That distinction is
why the guard below checks the cheap half and the six call sites were read by
hand for the other.

WHY IT HAPPENED. Each adapter wrote its own drop report, and nothing ever required
a loss report to identify its loss. The information was available at every one of
these sites — `text_len` is computed eight lines above, in an ENTRY line at
DEBUG. Production runs at INFO, so that line does not exist when it is needed;
this repo's own rule ("a `log.*.debug` line does not exist when you need it") was
being broken by the pairing rather than by either line alone.

WHAT THIS GUARD DOES NOT CHECK, stated rather than hidden: whether the payload is
INFORMATIVE. `{"has_app": true}` passes it. Whether a field varies per event is
not decidable from the source, and a guard that guessed would cry wolf on correct
work — the failure this programme keeps paying for. Presence is mechanically
checkable and absence is unarguable; the informative half is a rule you apply.

WHY THIS ONE CAN BE A GATE. The population is ZERO after this change and stays
zero unless somebody writes a NEW loss report with nothing in it, so it can only
fire on the change that causes it.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"

#: The vocabulary a message uses when it says work was thrown away. Deliberately
#: SMALL and explicit: a wide regex over log text would sweep in every sentence
#: that merely mentions loss, and a gate that fires on prose is a gate people
#: learn to route around.
_CLAIMS_A_LOSS = re.compile(
    r"\b(dropped|discard\w*|not delivered|lost|losing|thrown away)\b", re.IGNORECASE
)
_LOUD = {"warning", "error", "critical", "exception"}


def _message_literal(node: ast.expr) -> str | None:
    """The static text of a log call's first argument, or None if it is dynamic.

    Handles the three shapes this tree actually uses — a plain string, an f-string,
    and implicit/explicit concatenation across wrapped lines. A dynamic message is
    not judged: the guard would be guessing at runtime text.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _message_literal(node.left), _message_literal(node.right)
        if left is None and right is None:
            return None
        return (left or "") + (right or "")
    return None


def _loss_reports() -> list[tuple[str, int, bool, str]]:
    """(file, line, carries_extra, message) for every loud log call claiming a loss.

    READS THE AST, NOT THE TEXT. A regex over source would match the sentence
    inside a docstring explaining the rule — which is how four guards in this
    session were broken by, or satisfied by, a comment.
    """
    out: list[tuple[str, int, bool, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — the syntax gate catches these first
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _LOUD:
                continue
            base = node.func.value
            # `log.<channel>.<level>(...)` — the named-logger convention this
            # repo mandates. A bare `logging.warning` is not one of ours.
            if not (
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id == "log"
            ):
                continue
            message = _message_literal(node.args[0]) if node.args else None
            if not message or not _CLAIMS_A_LOSS.search(message):
                continue
            carries = any(kw.arg == "extra" for kw in node.keywords)
            out.append((str(path.relative_to(_SRC)), node.lineno, carries, message))
    return out


@pytest.mark.tripwire
def test_every_reported_loss_carries_a_payload() -> None:
    reports = _loss_reports()
    assert reports, (
        "the walk found NO loss reports at all, which means the detector stopped "
        "matching rather than the tree becoming clean — a zero numerator over a "
        "zero denominator is not a pass"
    )
    silent = [f"{f}:{ln}  {msg[:88]}" for f, ln, carries, msg in reports if not carries]
    assert not silent, (
        "these lines announce that something was thrown away and carry nothing "
        "that says WHICH thing. A loss nobody can identify cannot be "
        "investigated — telegram's equivalent fired 61 times and every record was "
        "byte-identical. Add `extra={\"_fields\": {...}}` naming the payload "
        "(a length, an extension, a caller):\n  " + "\n  ".join(silent)
    )


@pytest.mark.tripwire
def test_the_detector_still_sees_the_population_it_was_built_on() -> None:
    """The denominator, pinned — because a guard whose matcher rots reads GREEN.

    This is the failure the assertion above cannot catch on its own: if
    `_CLAIMS_A_LOSS` or the `log.<name>` shape stops matching, `silent` is empty
    and the gate passes while guarding nothing. 41 was the measured population on
    2026-09-08; it may grow, and a floor is the honest pin.
    """
    assert len(_loss_reports()) >= 35, (
        f"only {len(_loss_reports())} loss reports matched, against 41 measured on "
        f"2026-09-08. Either a great many were deleted, or the detector no longer "
        f"recognises the shape it was built to find."
    )
