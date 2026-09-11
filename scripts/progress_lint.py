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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


def log_pattern_fragment(pattern: str) -> str:
    """The literal text a `log.*` call would actually contain, from a check's grep pattern.

    ONE SOURCE, for the same reason `entries_with_closing_checks` is one: three guards ask
    "does anything emit this?" and each was doing its own extraction.

    THE ENVELOPE IS THE WHOLE PROBLEM. These patterns match a LOG LINE, so they carry the
    JSON the formatter adds at WRITE time — `"msg": "…"`, `"code": "…"` — and that text
    appears in no source file. A guard searching for the raw pattern therefore finds no
    emitter and concludes the evidence cannot exist, for a string the logs are full of.

    MEASURED 2026-09-07, when five `current` closing checks became visible for the first
    time and TWO separate guards failed on them at once: the emitter walk said "no logging
    call in `src/` emits this" about `[resilient_round] fault classified`, whose own check
    had just counted FORTY-FIVE live log lines. A guard that calls a demonstrably-emitted
    string unemittable is worse than no guard, because it fails correct work.

    Regex alternation and `.*` are cut too, so the fragment is a literal a source string
    can plausibly contain.
    """
    frag = re.split(r"\\\||\.\*", pattern)[0].replace("\\", "").strip()
    inner = re.search(r'"(?:msg|code)":\s*"(.+)', frag)
    if inner:
        frag = inner.group(1).rstrip('"').strip()
    return frag


def entries_with_closing_checks(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Every ``(label, closing_check)`` in the record, items AND known_debt.

    ONE SOURCE, because there are now three readers — ``validate_check.py`` and two
    audit guards — and the previous shape had each of them iterate ``items`` on its
    own. Adding a second population to three private loops is the two-copies defect
    this file exists to catch, so the union lives here and the readers ask.

    Evidence-led work is recorded in ``known_debt`` rather than as an item, so its
    claims had no way to be re-checked: a `partial` item gets ``validate_check.py``
    re-running its query every loop, and a debt got a paragraph. That is the same
    dead-end DEBT-124 removed for items, one population over.

    AND ``current`` WAS THE THIRD POPULATION, MISSING FROM THE UNION ITSELF. The
    paragraph above is exactly right about why one source exists, and the source knew
    two of the record's three sections. MEASURED 2026-09-07: FIVE ``current`` entries
    were ``validate: partial`` with a perfectly good executable check that NOTHING ran —
    four of them written earlier the same day. Wiring them in, three were immediately
    CLOSEABLE on evidence that had already arrived and that no reader could see.

    ``current`` is a MAPPING keyed by record name where the other two are lists of
    records carrying their own ``id``, which is why it was easy to leave out and why the
    key is used as the label here.
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
    for name, rec in (data.get("current") or {}).items():
        if not isinstance(rec, dict):
            continue
        check = (rec.get("closing_check") or "").strip()
        if check:
            out.append((str(name), check))
    return out


#: A key naming the LIVE evidence behind a `validate: done`. The prefix is the whole
#: mechanism, and it is deliberately a NAME rather than a content heuristic.
#:
#: MEASURED 2026-09-08, on my own record and by accident: a first attempt scored an
#: entry as evidenced if it carried any key matching /valid|live|observed|measured/ and
#: longer than 120 characters. It passed DEBT-230 — whose only matching key was
#: `the_live_cost_is_on_the_adapter_that_DID_carry_a_payload`, a description of the
#: DEFECT — while that entry's validate had never happened at all. A key whose NAME
#: merely resembles evidence is not evidence, which is this repo's denominator rule
#: reaching the instrument that was meant to enforce it.
#: A record names its live evidence in a `validate…` key. MATCHED BY SHAPE, NOT BY
#: ONE SPELLING — this read `"validated"` (with the d) until 2026-09-11, while the
#: corpus overwhelmingly writes `validate_` (without it). MEASURED: of the 43 records
#: this reported as unevidenced, **17 named their evidence perfectly well** under
#: `validate_result` (10), `validate_evidence_<date>` (3), a bare `validate` (3),
#: `validate_CLOSED_<date>` (2) and `validate_done_because` (1). A ceiling that is 40%
#: false is worse than none, because it is the number a later loop ratchets against.
#: That is DEBT-303's own cause one instrument over: a rule matching a hand-chosen
#: STRING rather than the SHAPE it is about.
#:
#: A key naming PARTIAL is EXCLUDED, and that exclusion is not cosmetic. Eight records
#: carried `validate: done` beside a `validate_is_PARTIAL_and_the_reason_is_named` key
#: — the record contradicting its own stage — so admitting it would credit a `done`
#: with the explanation of why it was not one. Those eight were re-keyed to the past
#: tense in the same change; this keeps the trap disarmed.
def _names_its_evidence(key: str) -> bool:
    return key.startswith("validate") and "PARTIAL" not in key



def entries_with_an_unevidenced_done_validate(data: dict[str, Any]) -> list[str]:
    """Every `validate: done` that names neither a document nor its live evidence.

    THE FOURTH INSTANCE OF ONE CURE. An escalation's premise aged silently until
    `premise_check`; a `partial` stage's evidence aged silently until `closing_check`;
    a document's claim aged silently until `doc_check`. A `validate: done` had no
    check of any kind — and a `done` is the one that stops anybody looking again.

    THE CASE THAT FOUND IT WAS MINE. DEBT-230's record was drafted in the scratchpad
    while a full suite ran and applied verbatim when the tree came free, so it
    asserted a validate that had not happened: the platform was never restarted and
    no live record was ever read. Nothing could have caught that, because nothing
    asked.

    TWO WAYS TO SATISFY IT, and both are honest. A mapped item's evidence belongs in
    its design document's Verification section, so a `doc:` key discharges the
    requirement. Evidence-led work has no document, so it names the evidence here in
    a `validated…` key — or records the stage `partial` with a `closing_check`, which
    is what the honest-validate rule prescribes for a claim reality has not settled.

    `current` IS EXCLUDED, and the exclusion is stated rather than hidden: it holds
    133 such records against 43 here, and it is a JOURNAL — this file's own summary
    calls 281 of its 294 keys "journal and prunable". Pinning a number made of two
    different kinds of record is the denominator error this programme pays for most.
    """
    out: list[str] = []
    for pool in ("items", "known_debt"):
        for entry in data.get(pool) or []:
            if not isinstance(entry, dict):
                continue
            if (entry.get("stages") or {}).get("validate") != "done":
                continue
            if entry.get("doc"):
                continue  # the document's Verification section is the evidence
            if not any(_names_its_evidence(k) for k in entry):
                out.append(str(entry.get("id")))
    return out


#: The convention the corpus already uses to write a closing query by hand. The COLON
#: is the discriminator and it is doing real work: ``the closing query returned 213`` is
#: narration about a check that ran, while ``Closing query: grep ...`` is a promise that
#: one will. Matching the bare phrase reports 117 records, nearly all of them the former.
_PROSE_PROMISE = re.compile(r"\bclosing (?:query|check)\s*:", re.I)

#: Keys whose value IS the executable check, so its text is not a prose promise.
_EXECUTABLE_KEYS = ("closing_check", "premise_check")


def _walk_strings(
    node: object, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Every ``(path, text)`` string in the document, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_strings(value, path + (str(index),))
    elif isinstance(node, str):
        yield path, node


def prose_closing_promises(data: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every ``(record, field, promise)`` written where nothing can execute it.

    THE FOURTH INSTANCE OF ONE CURE, and it is here because the first three were each
    keyed to the wrong thing. An escalation's premise aged silently until
    ``premise_check``; a ``partial`` stage's evidence aged until ``closing_check``; a
    design document's ``Last verified`` aged until ``doc_check.py``. Each time the fix
    was attached to a STATUS — the entry is an escalation, the stage is partial — when
    the property it actually protects is *this claim is not yet evidenced*, and that
    property does not care what status the record carries.

    MEASURED 2026-09-06, and the archetype is DEBT-105. It is recorded
    ``no_change_needed``, and its own text says: "55 heavy rounds after the fix, 19 of
    them telegram. That is a small sample and the direction is what is claimed, not the
    magnitude. Closing query: re-run the same before/after split over a full day of
    traffic on 2026-09-02." Four days passed. Nothing ran it, because nothing ENUMERATED
    it: ``validate_check.py`` walks items whose stage is ``partial``, plus debts that
    ALREADY carry a ``closing_check``. A settled record holding a prose promise is
    invisible to the one tool built to stop promises being written in prose.

    Twenty-three such records exist, each read by hand rather than counted. When the
    query finally ran it did not overturn the decision — the fix works, and the control
    channel barely moved — but it did correct the record: the "77% reduction" compared
    telegram AFTER against the all-channel BEFORE, two different populations. Like for
    like it is 39%. That is what four unenumerated days cost, and it is the cheap case.

    A QUOTED promise reads as a new one — the record fixing this hit it immediately by
    citing the archetype's words. The detector cannot tell a quotation from a commitment
    and does not try, because guessing would make it unreliable in the one direction
    that matters. Quote a closing query without its marker.

    REPORTS, NEVER GATES, and that is the ``doc_check.py`` precedent applied on purpose.
    A tripwire failing on all 23 would fail every unrelated change until someone wrote
    23 queries, several of which genuinely cannot be written yet because they wait on
    traffic that has not happened. That is how a gate gets bypassed rather than
    satisfied. Visibility is the whole fix: the reason DEBT-105 sat is that no loop ever
    printed it.
    """
    covered = _records_carrying_an_executable_check(data)
    out: list[tuple[str, str, str]] = []
    for path, text in _walk_strings(data):
        if path and path[-1] in _EXECUTABLE_KEYS:
            continue
        match = _PROSE_PROMISE.search(text)
        if not match:
            continue
        record = _record_label(data, path)
        if record in covered:
            # The record already has a runnable check; this prose is narration beside
            # it, not an orphan promise. Reporting it would train the reader to skim.
            continue
        promise = " ".join(text[match.end():].split())
        out.append((record, ".".join(path), promise))
    return out


def _record_label(data: dict[str, Any], path: tuple[str, ...]) -> str:
    """A name a human can search for, never a list index.

    ``known_debt`` is a LIST, so the natural label for its entries is the position —
    which is meaningless to a reader and changes whenever an entry is inserted above.
    The entry's own ``id`` is the durable name, so this resolves to it and falls back
    to the position only when there is none.
    """
    if not path:
        return "<root>"
    if path[0] in ("items", "known_debt") and len(path) > 1:
        try:
            entry = data[path[0]][int(path[1])]
        except (KeyError, IndexError, ValueError, TypeError):
            return f"{path[0]}[{path[1]}]"
        if isinstance(entry, dict) and entry.get("id"):
            return str(entry["id"])
        return f"{path[0]}[{path[1]}]"
    return path[1] if len(path) > 1 else path[0]


def _records_carrying_an_executable_check(data: dict[str, Any]) -> set[str]:
    """Records that already have a runnable check, so their prose is not an orphan."""
    covered: set[str] = set()
    for path, _text in _walk_strings(data):
        if path and path[-1] in _EXECUTABLE_KEYS:
            covered.add(_record_label(data, path))
    return covered


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


def stale_stage_problems(data: dict[str, Any]) -> list[str]:
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


def load_bearing_current_keys(data: dict[str, Any]) -> dict[str, str]:
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


def unevidenced_validate_problems(data: dict[str, Any]) -> list[str]:
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


def partial_without_closing_check_problems(data: dict[str, Any]) -> list[str]:
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


def stale_closing_check_problems(data: dict[str, Any]) -> list[str]:
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


def misattributed_doc_problems(data: dict[str, Any]) -> list[str]:
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

    # A `done` validate is the one nobody looks at again, so it is the one that has
    # to name its evidence. REPORTED, not refused: 43 legacy records predate the
    # convention, and a linter that failed on all of them would be bypassed rather
    # than satisfied. `tests/audit/test_a_done_validate_names_its_evidence.py`
    # ratchets the number so it can only fall.
    unevidenced = entries_with_an_unevidenced_done_validate(data)
    if unevidenced:
        print(
            f"  validate: {len(unevidenced)} `done` with neither a design document nor "
            f"a `validated…` key — evidence nobody can find is a claim, not a check"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
