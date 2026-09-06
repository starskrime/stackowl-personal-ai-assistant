#!/usr/bin/env python3
"""Is progress.yml actually recording what it looks like it records?

WHY THIS EXISTS. On 2026-08-08 a full slice of D10.2 work was written into that
item's ``changes:`` list — and vanished. The item carried the key TWICE, once
near the top and once after ``decisions:``, both empty. YAML keeps the last
occurrence, so the write landed in the copy that was immediately overwritten by
the empty one. ``yaml.safe_load`` reported zero changes with no error, no
warning, and no indication that anything had been discarded.

Seven items were in that state. All fourteen keys happened to be empty, so
nothing had been lost yet — but the file's whole job is to be the state of
record, and a state of record that silently drops writes is worse than one that
is merely out of date, because it looks current.

This is the same shape as every other defect this programme keeps finding: the
write happens, the effect does not, and nothing says so. So it becomes a check.

USAGE

    uv run python scripts/progress_lint.py

Exit status is 1 when something is wrong, 0 when the file is sound.
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys
from pathlib import Path

import yaml

_STAGES = (
    "brainstorm", "architect", "implement", "cleanup", "test", "validate", "document",
)
#: ``no_change_needed`` is PROCESS.md's stated legitimate outcome; ``blocked``
#: is in use for a stage waiting on something outside the programme (D05.8's
#: architect stage needs live interactive traffic). Both are real states, not
#: typos — the linter's job is to catch a stage value nobody meant to write,
#: which means the vocabulary has to match what the process actually uses.
#: ``pre_process_exception`` records a stage that never ran because the item
#: PREDATES the process that requires it. D01.6 was the first item worked and was
#: built on 2026-07-25, the same day the grill rule itself was written; its
#: brainstorm never happened. ``done`` would be a lie and ``no_change_needed``
#: claims the stage examined the item and found nothing to change, which is also
#: not what occurred. Bakir accepted it as a documented exception on 2026-08-15,
#: so the vocabulary gains a word for it rather than an existing word being bent
#: to cover a case it does not mean.
_VALID_STAGE_VALUES = frozenset({
    "done", "partial", "not_started", "no_change_needed", "blocked",
    "pre_process_exception",
})


_ITEM_ID_RE = re.compile(r"D\d{2}\.\d+")
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _describe(node: yaml.MappingNode) -> str:
    """A human label for the mapping a duplicate was found in.

    Prefers the mapping's own ``id`` (items) so a message reads "D10.2: 'changes'
    appears 2 times"; falls back to the line number, which is what makes a
    duplicate under ``current:`` findable at all.
    """
    for key, value in node.value:
        if getattr(key, "value", None) == "id" and isinstance(value, yaml.ScalarNode):
            return str(value.value)
    return f"line {node.start_mark.line + 1}"


def entries_with_closing_checks(data: dict) -> list[tuple[str, str]]:
    """Every ``(label, closing_check)`` in the record, items AND known_debt.

    ONE SOURCE, because there are now three readers — ``validate_check.py`` and two
    audit guards — and the previous shape had each of them iterate ``items`` on its
    own. Adding a second population to three private loops is the two-copies defect
    this file exists to catch, so the union lives here and the readers ask.

    Evidence-led work is recorded in ``known_debt`` rather than as an item, so its
    claims had no way to be re-checked: a `partial` item gets ``validate_check.py``
    re-running its query every loop, and a debt got a paragraph. That is the same
    dead-end DEBT-124 removed for items, one population over.
    """
    out: list[tuple[str, str]] = []
    for item in data.get("items", []):
        check = (item.get("closing_check") or "").strip()
        if check:
            out.append((str(item.get("id")), check))
    for debt in data.get("known_debt", []) or []:
        check = (debt.get("closing_check") or "").strip()
        if check:
            out.append((str(debt.get("id")), check))
    return out


def duplicate_key_problems(text: str) -> list[str]:
    """Every key that appears twice in the SAME mapping, at any depth.

    WIRED ON ONLY SOME PATHS UNTIL 2026-08-21. The first version of this check was
    textual — ``^  - id: `` to find item blocks, then ``^    (\\w+):`` for their keys
    — so it inspected items and nothing else. ``current:``, which holds ``stage``,
    ``ESCALATIONS`` and every hand-off note and is the most-written block in the file,
    was never looked at. It carried ``ESCALATIONS:`` twice (lines 425 and 699); the
    first held D16.3's entire brainstorm escalation record and was discarded at every
    load, while this script printed "no duplicate keys".

    ``yaml.compose`` builds the node tree BEFORE duplicate keys are merged away, so
    every mapping is checked exactly, at any depth, with no indent assumptions. That
    also retires the ``_NESTED_OK`` allow-list: it existed only because "the flat
    regex cannot tell nesting apart", and a composer can — ``status`` appearing in two
    different items is two mappings, not a duplicate.
    """
    try:
        root = yaml.compose(text)
    except Exception as exc:  # noqa: BLE001 — the message is the output
        return [f"does not parse: {exc}"]

    problems: list[str] = []
    stack: list[yaml.Node] = [root] if root is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            seen: collections.Counter[str] = collections.Counter(
                str(k.value) for k, _v in node.value if isinstance(k, yaml.ScalarNode)
            )
            where = _describe(node)
            for key, n in sorted(seen.items()):
                if n > 1:
                    problems.append(
                        f"{where}: '{key}' appears {n} times — YAML keeps the LAST, "
                        f"so a write to any earlier copy is discarded without an error"
                    )
            stack.extend(v for _k, v in node.value)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
    return problems


def stale_stage_problems(data: dict) -> list[str]:
    """Items the loop WORKED but whose structured stages never moved.

    WHY THIS IS A CHECK. An item's progress lives in two places: the narrative
    record under ``current``, which every loop writes, and the ``stages`` map
    under ``items``, which the loop is supposed to update after EVERY stage. Only
    the first was being written. Measured 2026-09-04: D04.4, D12.8 and N01 — the
    three most recent items — each carried a full worked record under ``current``
    (measurements, what shipped, suites, a validate verdict) while every one of
    their seven stages still read ``not_started`` and ``changes`` was empty.

    That is not cosmetic. **The loop chooses its next item FROM THESE STAGES.** A
    finished item that still reads not_started gets picked again; and the count of
    what remains is wrong in the direction that hides work. Same shape as every
    other defect here: two copies of one fact, one of them written, and nothing
    asking whether they agree.
    """
    problems: list[str] = []
    current = data.get("current") or {}
    keys = [k for k in current if isinstance(k, str)]
    for item in data.get("items", []):
        ident = str(item.get("id", ""))
        if not ident:
            continue
        prefix = ident.replace(".", "_")
        records = [
            k for k in keys
            if k == prefix or k.startswith(f"{prefix}_")
        ]
        if not records:
            continue
        stages = item.get("stages") or {}
        # NOT "every stage is not_started". That was the first version of this
        # check and it was too weak in a way it demonstrated immediately: a
        # repair script rewrote ONE stage line of each item — the rest use
        # aligned padding its regex missed — and this check went green over a
        # state still recording implement/test/validate as never started. A guard
        # that passes on a known-wrong state is worse than none, because it is
        # now the reason nobody looks. These three stages cannot be skipped by an
        # item whose work is written down, so any one of them left at
        # 'not_started' beside a worked record is the divergence.
        unstarted = [s for s in ("architect", "implement", "test")
                     if stages.get(s) == "not_started"]
        if stages and unstarted:
            problems.append(
                f"{ident}: stage(s) {', '.join(unstarted)} read 'not_started' but "
                f"current holds {len(records)} worked record(s) — e.g. "
                f"'{records[0][:56]}'. The loop picks its next item from these "
                f"stages, so a finished item recorded this way is picked again."
            )
    return problems


#: Stages whose `done` is a CLAIM ABOUT THE WORLD rather than about the tree, so
#: a reader must be able to check it. `implement: done` is checked by the tests;
#: these two are not checked by anything but the record they leave.
_CLAIM_STAGES = ("validate", "document")

#: What counts as evidence for a claim stage, BESIDES a ``current`` record. One
#: source, because :func:`unevidenced_validate_problems` and
#: :func:`load_bearing_current_keys` must agree on it exactly — the second decides
#: which records may be pruned, and a disagreement would mark a load-bearing key
#: prunable.
_EVIDENCE_FIELDS = ("doc", "validate_result", "changes", "notes", "decisions")


def load_bearing_current_keys(data: dict) -> dict[str, str]:
    """Item id -> the ``current`` key that is its ONLY evidence.

    WHY THIS IS SEPARATE FROM THE LINT RULE. ``current`` is 41% of a 2.5 MB
    ``progress.yml`` and only grows, which is failure mode #4 running inside the
    state of record. It has not been pruned because the block does TWO jobs under
    one keyspace: measured 2026-09-06, 64 of its 230 keys are per-item evidence
    that :func:`unevidenced_validate_problems` accepts as proof of a ``done``
    claim, and 166 are a loop journal (``DEBT…``, ``ESC…``,
    ``OPERATOR_ANSWERED…``) that nothing depends on.

    For FOURTEEN items the record is the only evidence there is — no doc, no
    changes, no decisions, no notes.

    **THE PRUNE IS ALREADY PROTECTED, and this function does NOT protect it.** The
    first version of this docstring claimed the linter "would not notice" a pruned
    key. That was written from reasoning rather than measurement and it is FALSE:
    simulated on 2026-09-06 by renaming
    ``D07_1_2026_09_04_parity_confirmed…``, :func:`unevidenced_validate_problems`
    failed the lint immediately with "D07.1: validate: done and document: done with
    no evidence". A tripwire written on that premise passed its own mutant, because
    it derived the load-bearing set from the very file it then checked — a guard
    that cannot fail. It was deleted rather than shipped.

    What was missing is not protection but VISIBILITY: nobody could see how much of
    the block was safe to touch, so nobody touched it, and it grew to 41%. This
    names the load-bearing subset so ``main`` can report the split, which turns the
    prune from a risk nobody takes into a decision someone can act on.

    ONE SOURCE: the evidence test here is the same expression
    :func:`unevidenced_validate_problems` uses, so the two cannot drift into
    different ideas of what counts as evidence.
    """
    current = data.get("current") or {}
    keys = [k for k in current if isinstance(k, str)]
    out: dict[str, str] = {}
    for item in data.get("items", []):
        ident = str(item.get("id", ""))
        if not ident:
            continue
        if any(item.get(f) for f in _EVIDENCE_FIELDS):
            continue  # evidenced elsewhere; its record is journal, not load-bearing
        records = [k for k in keys if k == ident.replace(".", "_")
                   or k.startswith(f"{ident.replace('.', '_')}_")]
        if len(records) == 1:
            out[ident] = records[0]
        elif records:
            # More than one record and no other evidence: every one of them is
            # load-bearing, so name them jointly rather than picking one.
            out[ident] = records[0]
    return out


def unevidenced_validate_problems(data: dict) -> list[str]:
    """A claim stage marked `done` with nothing recorded that a reader could check.

    THE CARDINAL RULE OF THIS PROGRAMME is that a claim in progress.yml must have
    been measured: "an autonomous loop that marks things done is only as
    trustworthy as its evidence." Nothing enforced it. `validate` is a bare enum,
    and writing `done` into it required no companion of any kind.

    Measured 2026-09-04: TWO items — D03.4 and D11.2, both P1 — carried
    `validate: done` with no doc, no `current` record, no validate_result, no
    changes, no notes and no decisions. Both turned out to be TRUE when
    re-measured against the tree and the logs, which is the point: the claims were
    right and UNVERIFIABLE BY INSPECTION, so the only way to trust them was to do
    the work again. Evidence that has to be re-derived is not a state of record.

    EXTENDED TO `document` ON 2026-09-04, after D03.5 was found reading
    `document: done` while `doc` was null — the same shape one stage over, which
    this check could not see because it only ever looked at `validate`. Nothing was
    actually wrong: measured with THIS function's own evidence set, zero items
    claim a done stage with nothing behind it. The extension is so that the rule is
    the same rule for both claim stages rather than one stage's special case.
    """
    problems: list[str] = []
    current = data.get("current") or {}
    keys = [k for k in current if isinstance(k, str)]
    for item in data.get("items", []):
        stages = item.get("stages") or {}
        claimed = [s for s in _CLAIM_STAGES if stages.get(s) == "done"]
        if not claimed:
            continue
        ident = str(item.get("id", ""))
        prefix = ident.replace(".", "_")
        has_record = any(k == prefix or k.startswith(f"{prefix}_") for k in keys)
        if has_record or any(item.get(f) for f in _EVIDENCE_FIELDS):
            continue
        problems.append(
            f"{ident}: {' and '.join(f'{s}: done' for s in claimed)} with no "
            f"evidence — no doc, no current record, no validate_result, changes, "
            f"notes or decisions. A claim nobody can check is not a state of record."
        )
    return problems


def partial_without_closing_check_problems(data: dict) -> list[str]:
    """A stage recorded `partial` that does not say what evidence would close it.

    THE SIBLING RULE, ONE STAGE VALUE OVER. `unevidenced_validate_problems` above
    refuses a `done` claim with nothing behind it — "a claim nobody can check is not
    a state of record." A `partial` is also a claim: it asserts that evidence is not
    yet available. Nothing required it to say what availability would look like.

    MEASURED 2026-09-06: ten stages in this file are `partial`, all of them
    `validate`, and NOT ONE carried a structured field naming its closing evidence.
    Three had a closing query written in English inside `changes:`, where nothing can
    run it; the other seven had nothing at all. Seven items were therefore parked
    permanently — not because the evidence was unavailable, but because no one had
    recorded what would count as evidence, so no later pass could ever check.

    This programme already solved exactly this problem for the other queue. Every
    escalation carries a `premise_check` printing HOLDS or EXPIRED, and
    `scripts/escalation_check.py` re-runs all of them at the start of every loop —
    built because "an escalation got written once, with a measurement, and was never
    looked at again." Validates got the discipline in prose and never in code.

    APPLIED TO EVERY STAGE, not just `validate`. All ten partials today are validate,
    and narrowing the rule to that fact would rebuild the blind spot the sibling rule
    above already had to be widened out of once, when a `document: done` defect went
    unseen because the check only ever looked at `validate`.
    """
    problems: list[str] = []
    for item in data.get("items", []):
        stages = item.get("stages") or {}
        partial = [s for s in _STAGES if stages.get(s) == "partial"]
        if not partial:
            continue
        if (item.get("closing_check") or "").strip():
            continue
        problems.append(
            f"{item.get('id', '<unknown>')}: {', '.join(partial)} is 'partial' with "
            f"no `closing_check` — a stage that cannot say what would close it can "
            f"never be advanced by anyone. Add a one-liner printing OPEN or "
            f"CLOSEABLE, the way every escalation carries a premise_check."
        )
    return problems


def stale_closing_check_problems(data: dict) -> list[str]:
    """A `closing_check` on an item that has no `partial` stage left.

    THE MIRROR OF THE RULE ABOVE, and it exists because closing a stage is exactly the
    moment the obligation reverses. While a stage is `partial` the check is REQUIRED;
    the moment it closes, the same field becomes a write with no reader —
    `validate_check.py` iterates only items carrying a partial stage, so a check left
    behind is never run again and never seen, while still reading as live evidence to
    anyone browsing the record. The thing it interrogates may have been deleted the day
    after.

    Written when D07.3's validate closed, because that close created the first instance.
    The evidence that closed a stage belongs in the item's `changes` — prose a reader
    can check — not in a command nothing will ever execute again.
    """
    problems: list[str] = []
    for item in data.get("items", []):
        if not (item.get("closing_check") or "").strip():
            continue
        stages = item.get("stages") or {}
        if any(stages.get(s) == "partial" for s in _STAGES):
            continue
        problems.append(
            f"{item.get('id', '<unknown>')}: carries a `closing_check` but has no "
            f"'partial' stage — nothing will ever run it again. Move what it proved "
            f"into `changes` and delete the field."
        )
    return problems


def misattributed_doc_problems(data: dict) -> list[str]:
    """An item pointing at ANOTHER item's design document.

    MEASURED 2026-09-05, and the measurement is that I did it. D18.6's completed
    stages, doc, decisions and changes were written into **D04.2's** record,
    because the edit was anchored on a block of `not_started` stages — a shape that
    occurs once per unworked item — instead of on the item's id. D04.2 was left
    claiming `document: done` against `designs/D18.6.md`.

    NOTHING CAUGHT IT. `stale_stage_problems` fired, but only about D18.6 still
    reading `not_started`; the FALSELY COMPLETED item passed every check, because a
    filled-in record with a doc and decisions is exactly what a finished item looks
    like. The one thing that did not match was the doc's NAME, and no rule read it.

    A design document is named for its item, so an item claiming a document whose
    filename belongs to a different item is claiming someone else's work. That is
    cheap to check and impossible to write by accident.
    """
    problems: list[str] = []
    for item in data.get("items", []):
        doc = item.get("doc")
        if not doc or not isinstance(doc, str):
            continue
        ident = str(item.get("id", ""))
        stem = pathlib.Path(doc).stem
        if not _ITEM_ID_RE.fullmatch(stem) or stem == ident:
            continue

        # A SHARED DOCUMENT IS LEGITIMATE AND SAYS SO. `DOC_STANDARD.md` rule 1 is
        # "one subsystem, one document", and `designs/D09.3.md` covers D09.3 and
        # D10.2 — naming both in its header. So the test is not the filename but
        # whether the document CLAIMS the item back. A record written into the
        # wrong item fails that immediately: `designs/D18.6.md` mentions D04.2
        # zero times.
        path = _ROOT / doc
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            problems.append(f"{ident}: claims doc {doc!r}, which does not exist.")
            continue
        if ident not in body:
            problems.append(
                f"{ident}: claims doc {doc!r}, which is named for {stem} and never "
                f"mentions {ident}. An item cannot own another item's design "
                "document — this is exactly what a record written into the wrong "
                "item looks like. A genuinely shared doc names both items."
            )
    return problems


def main() -> int:
    path = Path(__file__).resolve().parent.parent / "progress.yml"
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    problems.extend(duplicate_key_problems(text))

    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — the message is the output
        print(f"✗ progress.yml does not parse: {exc}")
        return 1

    problems.extend(stale_stage_problems(data))
    problems.extend(unevidenced_validate_problems(data))
    problems.extend(partial_without_closing_check_problems(data))
    problems.extend(stale_closing_check_problems(data))
    problems.extend(misattributed_doc_problems(data))

    for item in data.get("items", []):
        ident = item.get("id", "<unknown>")
        stages = item.get("stages") or {}
        missing = [s for s in _STAGES if s not in stages]
        if missing and stages:
            problems.append(f"{ident}: stages missing {', '.join(missing)}")
        for stage, value in stages.items():
            if value not in _VALID_STAGE_VALUES:
                problems.append(
                    f"{ident}: stage '{stage}' is {value!r}, not one of "
                    f"{sorted(_VALID_STAGE_VALUES)}"
                )

    if problems:
        print(f"✗ {len(problems)} problem(s) in progress.yml:\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"✓ progress.yml sound — {len(data.get('items', []))} items, no duplicate keys")

    # WHAT OF THE RECORD IS SAFE TO PRUNE. `current` only grows — failure mode #4
    # inside the state of record — and it stayed unpruned because nobody could see
    # which keys a `done` claim depends on. Printing the split makes the prune a
    # decision someone can act on instead of a risk nobody takes.
    current = data.get("current") or {}
    bearing = load_bearing_current_keys(data)
    total = sum(1 for k in current if isinstance(k, str))
    if total:
        print(
            f"  current: {total} keys — {len(bearing)} load-bearing (an item's ONLY "
            f"evidence), {total - len(bearing)} journal and prunable"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
