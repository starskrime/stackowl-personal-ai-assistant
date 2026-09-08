"""A number with ONE owner, restated in prose, drifts — and nothing was checking the copies.

WHY THIS EXISTS. `scripts/tripwires.sh` owns two lint baselines and refuses a commit that
raises either. Those numbers were also written out in prose across the repo, and the prose
went stale every time the underlying count moved.

MEASURED 2026-09-07, and the shape of the number is the point:

  * **The gate's values have NEVER changed** — 0 changes across the 2 commits that ever
    touched `scripts/tripwires.sh`; it was created already at its improved values. What
    moves is the COUNT the gate tracks, and the prose that copied it: ruff 46 -> 39 -> 37
    -> 35 and mypy 78 -> 65, five transitions.
  * **A mean of 12.2 markdown surfaces went stale per transition** — 11, 12, 12, 13, 13 —
    61 stale-surface-events in total, and the stale set grew monotonically because it is
    essentially the same dozen files each time. Every one was caught by a human reading,
    or not at all.
  * **0 guards.** Nothing in `tests/` or `scripts/` parsed the gate's numbers, over 30 test
    files that read something under `scripts/`. The nearest existing test asserts only that
    the STRINGS "ruff" and "mypy" appear in the gate body, so the gate could have been
    edited to `must not rise above 500` and stayed green.

WHAT IT COST. On 2026-09-07 three live instruction surfaces stated a wrong baseline, and
the worst was `docs/reference-mapping/PROCESS.md` — the method document every item is told
to read first — which said the ruff baseline was 39 against a gate of 35, inside a sentence
asserting *"these are the numbers `scripts/tripwires.sh` actually gates on"* and a note
claiming the figure had already been corrected. A reader following the prose believed they
had four findings of headroom the gate would refuse, on a tree that has ZERO headroom:
ruff 35/35 and mypy 65/65.

THE REFERENCE PLATFORM STATES ZERO LINT THRESHOLDS IN PROSE — and the reason is not the
one I first wrote down. I recorded that it "tolerates no pre-existing findings"; that was
inferred from a grep that found no numbers, and a panel lens caught it. What its
`pyproject.toml` actually does is set `select = ["PLW1514"]` under a comment reading *"All
other lints are intentionally disabled … while we wrangle typechecks"*. So it has a
threshold too — it simply expresses it as CONFIGURATION rather than as a tolerated count
restated in prose, and configuration has one owner by construction. That is the lesson, and
it is a stronger one than "reach zero": draining `DEBT-1` would leave `must not rise above
0` still living in exactly one place and still liable to be restated, so this guard is not
a workaround that expires.

HOW THE CLAIM IS SCOPED, and every boundary here was measured rather than chosen:

  * **A claim is a SENTENCE, not a line and not a paragraph.** Line scope catches 2 of the
    3 known defects: PROCESS.md's number sits on a hard-wrapped line whose neighbours carry
    the words `ruff` and `mypy`, so a per-line reader never sees a subject. Paragraph scope
    catches 1 of 3, because one wrong sentence sits in a paragraph where ANOTHER sentence
    names the owner and clears the whole block. Sentences catch 5 of 5. This is DEBT-208's
    lesson — scope a claim to its own span — arriving from both sides at once.
  * **The subject may be the linter OR its debt id.** `D01.7` said "the 78-error
    pre-existing baseline (`DEBT-8`)" and named no tool at all; a tool-word rule cannot see
    it. `DEBT-1`/`DEBT-8` are structured ids this repo assigns, not a phrasing guess.
  * **Only baseline-SIZED integers count**, and never one inside a ratio or a thousands
    group. Without that, `44/44 tests`, `step 4` and `7,871 bytes` all read as baselines.
  * **A sentence carrying an ISO DATE or naming the gate is cleared.** A document fixing
    this defect has to QUOTE the retired number to explain it, and that is a record, not a
    claim — the same false positive `_evidence_older_than_the_logs` had to design out.

PROVEN IN BOTH DIRECTIONS. Against the tree at `c5c8e3dd`, before the corrections, it names
all five real sites. Against the tree today it names none, over a live denominator this
test asserts is non-trivial — a rule that matched nothing would pass silently, which is the
failure this repo pays for most.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: The single owner. Both numbers are parsed out of the lines that ENFORCE them, so this
#: test cannot disagree with the gate the way the prose did.
_GATE_LINE = re.compile(r"must not rise above (\d+)")

#: Whose baseline. The debt ids are here because `D01.7` named one and no tool.
_SUBJECT = re.compile(r"\b(ruff|mypy)\b|\bDEBT-[18]\b", re.I)
_BASELINE = re.compile(r"baselines?\b", re.I)

#: A baseline-sized integer, never part of a ratio (`44/44`), a thousands group (`7,871`),
#: a decimal, or a percentage. Every value these baselines have ever held is 35..78.
_NUMBER = re.compile(r"(?<![\d,./])(\d{2,3})(?![\d,./%])")

_DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
_OWNER = re.compile(r"tripwires\.sh")
_SENTENCE = re.compile(r"(?<=[.;:!?])\s+")

#: Dated RECORDS of what was instructed at the time. Rewriting them would falsify history,
#: which is the boundary `CLAUDE.md` already draws: fix LIVE INSTRUCTION, leave the RECORD.
_RECORD_DIRS = (
    "do_not_push_to_git_research_only/",
    "_bmad-output/",
    "docs_archive_ralph",
    "docs/superpowers/",
)


def _gate_numbers() -> set[str]:
    body = (_ROOT / "scripts" / "tripwires.sh").read_text(encoding="utf-8")
    return set(_GATE_LINE.findall(body))


def _tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [_ROOT / f for f in out if not any(d in f for d in _RECORD_DIRS)]


def _baseline_claims(text: str) -> list[tuple[int, str, set[str]]]:
    """(line_no, sentence, baseline-sized integers) for every sentence claiming a baseline.

    Paragraphs are joined before splitting into sentences, because a hard-wrapped line is
    not a claim — the subject and its number routinely sit on different lines.
    """
    claims: list[tuple[int, str, set[str]]] = []
    block: list[str] = []
    start = 1

    def flush(block: list[str], start: int) -> None:
        if not block:
            return
        joined = " ".join(line.strip() for line in block)
        for sentence in _SENTENCE.split(joined):
            if not (_SUBJECT.search(sentence) and _BASELINE.search(sentence)):
                continue
            nums = {n for n in _NUMBER.findall(sentence) if 20 <= int(n) <= 199}
            if nums:
                claims.append((start, sentence, nums))

    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            if not block:
                start = i
            block.append(line)
        else:
            flush(block, start)
            block, start = [], i + 1
    flush(block, start)
    return claims


def _offending_numbers(sentence: str, nums: set[str], gate: set[str]) -> set[str]:
    """The baseline values in this sentence that contradict the gate. Empty means fine.

    ONE PREDICATE, asked by both the corpus sweep and the pinned-shape tests. It is here
    because the two disagreed once: a threshold bug that cut corpus recall from 5 of 5 to
    1 of 5 left every pinned test green, since those tests had reimplemented the decision
    instead of asking for it. That is this repo's two-copies-of-one-rule defect, committed
    inside a guard written to catch a two-copies-of-one-rule defect.
    """
    if _OWNER.search(sentence) or _DATED.search(sentence):
        return set()
    # AGAINST THE LOWEST ENFORCED CEILING. A sentence rarely binds its number to one tool
    # ("ruff and mypy baselines are 37 and 65"), so the only safe comparison is against the
    # smallest ceiling any of them could mean. Using max() measured a ruff claim of 37
    # against the mypy ceiling of 65 and cleared it.
    floor = min(int(g) for g in gate)
    # ONLY ABOVE. A baseline may FALL — DEBT-1 improved four times — so failing on any
    # disagreement would redden every unrelated commit the day a count drops, which is how
    # a gate gets bypassed rather than satisfied. It also targets the actual harm: a number
    # ABOVE the ceiling promises headroom the gate will refuse, which is what 39-against-35
    # did. Below it is merely conservative.
    return {n for n in nums if int(n) > floor and n not in gate}


def _disagreements(root: Path, gate: set[str]) -> tuple[int, list[str]]:
    seen = 0
    bad: list[str] = []
    for path in _tracked_markdown() if root == _ROOT else sorted(root.rglob("*.md")):
        rel = str(path.relative_to(root))
        if any(d in rel for d in _RECORD_DIRS):
            continue
        for line_no, sentence, nums in _baseline_claims(
            path.read_text(encoding="utf-8", errors="ignore")
        ):
            seen += 1
            over = _offending_numbers(sentence, nums, gate)
            if not over:
                continue
            bad.append(f"{rel}:~{line_no} states {sorted(over)} — {sentence[:120]}")
    return seen, bad


@pytest.mark.tripwire
def test_no_document_states_a_baseline_the_gate_does_not_enforce() -> None:
    gate = _gate_numbers()
    assert gate, "could not read the baselines out of scripts/tripwires.sh"
    seen, bad = _disagreements(_ROOT, gate)
    assert not bad, (
        f"{len(bad)} baseline claim(s) disagree with the gate ({sorted(gate)}). "
        "Do not restate the number — point at its owner, or date the sentence if it is a "
        "record of a past reading:\n  " + "\n  ".join(bad)
    )
    # THE DENOMINATOR. A rule that matched nothing would pass silently and forever, which
    # is exactly how the report this guard is modelled on read zero for a whole loop while
    # it was blind.
    assert seen >= 8, f"only {seen} baseline claims seen — the rule has gone blind"


#: The five real sites, VERBATIM from the tree at `c5c8e3dd`. The live corpus is clean, so
#: a weakened rule would keep passing against it forever — these are what actually hold the
#: guard to its job. Three shapes, and each defeats a different simpler rule: a wrapped
#: sentence whose subject is on another line, a claim whose only subject is a debt id, and
#: a narrative that quotes the retired number.
_REAL_DEFECTS = [
    "- ruff baseline in src/ is 37 and mypy is 65.",
    "- ruff and mypy baselines are 37 and 65 in src/.",
    "The baseline is now **39** (was 46) and `mypy` is **65**;",
    "Zero new errors remain against the 78-error pre-existing baseline (`DEBT-8`).",
    "**`SESSION_PROMPT.md`**, which is the prompt handed to an autonomous session and "
    "which also stated the ruff baseline as 37 when it is 35.",
]

#: Shapes that must NOT flag. Each was a measured false positive of a simpler rule.
_MUST_NOT_FLAG = [
    # a ratio of test counts, not a baseline
    "targeted 44/44 tests + ruff + mypy (0 new errors, git-stash baseline compared).",
    # the record form: a dated reading is evidence, not a claim about today
    "# RAN 2026-08-08 -> 39 and 78. RAN 2026-08-17 -> ruff 39, mypy 65.",
    # deferring to the owner is the whole point of the fix
    "the standing ruff/mypy baselines enforced by `scripts/tripwires.sh`. DIFF against it.",
    # agreeing with the gate is fine
    "ruff 35 / mypy 65 are the baselines.",
]


@pytest.mark.tripwire
@pytest.mark.parametrize("sentence", _REAL_DEFECTS)
def test_it_catches_every_defect_it_was_built_for(sentence: str) -> None:
    """A guard that cannot be shown to catch the thing it was built for is decoration."""
    gate = {"35", "65"}
    claims = _baseline_claims(sentence)
    assert claims, f"not even seen as a baseline claim: {sentence!r}"
    _, sent, nums = claims[0]
    assert _offending_numbers(sent, nums, gate), (
        f"seen but not flagged: {sentence!r} -> {sorted(nums)}"
    )


@pytest.mark.tripwire
@pytest.mark.parametrize("sentence", _MUST_NOT_FLAG)
def test_it_does_not_cry_wolf_on_the_shapes_that_are_correct(sentence: str) -> None:
    """Each of these was a measured false positive of a simpler rule. A guard that fires on
    correct work is the failure this programme keeps paying for."""
    gate = {"35", "65"}
    for _, sent, nums in _baseline_claims(sentence):
        assert not _offending_numbers(sent, nums, gate), (
            f"cried wolf on {sentence!r} -> {sorted(nums)}"
        )


@pytest.mark.tripwire
def test_a_wrapped_claim_is_read_as_one_sentence_not_as_lines() -> None:
    """The PROCESS.md shape, which is why this reads sentences rather than lines.

    The number sits on a hard-wrapped line whose subject words live on the lines above and
    below it. A per-line rule sees a number with no subject and says nothing; a
    per-paragraph rule sees a neighbouring sentence naming the gate and clears the lot.
    Measured: line scope caught 2 of 3, paragraph scope 1 of 3, sentences 5 of 5.
    """
    doc = (
        "`DEBT-1` — pre-existing `ruff` errors in `src/`, in files unrelated to any\n"
        "current item. The baseline is now **39** (was 46) and `mypy` is **65**; both\n"
        "are checked before every change.\n"
    )
    claims = _baseline_claims(doc)
    assert any("39" in nums for _, _, nums in claims), claims


@pytest.mark.tripwire
def test_a_neighbouring_sentence_does_not_clear_a_wrong_one() -> None:
    """The PROCESS.md shape VERBATIM, and the only test that distinguishes sentence scope
    from paragraph scope.

    One paragraph, two sentences: the first states a wrong baseline, the second names the
    gate and carries a date. Under paragraph scope the second CLEARS the first and the
    defect that motivated this whole guard goes unseen — which is what actually happened,
    for a day, in the method document every item is told to read first. Measured over the
    corpus at `c5c8e3dd`: sentences 5 of 5, lines 2 of 5, paragraphs 1 of 5.

    Kept because a mutation to paragraph scope survived every other test in this file.
    """
    gate = {"35", "65"}
    doc = (
        "`DEBT-1` — pre-existing `ruff` errors in `src/`, in files unrelated to any\n"
        "current item. The baseline is now **39** (was 46) and `mypy` is **65**; both\n"
        "are checked before and after every change, and neither may rise.\n"
        "**These are the numbers `scripts/tripwires.sh` actually gates on** — corrected\n"
        "2026-09-06, when the prose here still said 39/78 against a gate of 35/65.\n"
    )
    offending = [
        sorted(_offending_numbers(sent, nums, gate))
        for _, sent, nums in _baseline_claims(doc)
    ]
    assert any(o for o in offending), (
        "the wrong sentence was cleared by its neighbour — this is paragraph scope, "
        f"not sentence scope: {offending}"
    )


# ---------------------------------------------------------------------------------------
# THE SECOND OWNED NUMBER. Everything above is about one number; this is the generalisation
# a panel lens asked for and the evidence that it was right to ask.
#
# MEASURED 2026-09-07, immediately after the baselines were cleaned: the MIGRATION COUNT is
# the worse live case. There are 138 `.sql` files, highest id `0139`, and of the SIX
# sentences in this corpus that state a migration count, **FIVE disagreed** — against zero
# for the baselines. They state 90, 135 and 136: three different wrong answers.
#
# AND THE MAP CONTRADICTS ITSELF IN ONE FILE. `REFERENCE_MAP.md:947` says "pool + 90
# migrations"; `REFERENCE_MAP.md:1882` says "there are **136** migrations, not 90". The
# correction was written into the same document as the error and never edited the error.
# `D11.1.md:22` then quotes it a third time — *"The map said 90 migrations. There are 135.
# Corrected."* — and the map still said 90, while 135 had itself gone stale. Each
# correction minted the next generation of wrong number without fixing the original. That
# is the cause this whole file exists for, and it is why the cure has to be "ask the
# owner", never "correct the copy".
#
# THE COUNT AND THE ID ARE TOLD APART BY POSITION, not by a word list. A count PRECEDES the
# plural noun ("138 migrations"); an id FOLLOWS the singular ("migration 0110"). Without
# that distinction the rule flags every legitimate reference to a specific migration —
# measured, 14 sentences, almost all of them `0102`/`0107`/`0110`/`0136` id references
# doing nothing wrong.
_MIGRATIONS = _ROOT / "src" / "stackowl" / "db" / "migrations"

#: "138 migrations" — a count, because it precedes the plural noun.
_MIGRATION_COUNT = re.compile(r"\*{0,2}(\d{2,4})\*{0,2}\s+migrations\b", re.I)
#: "there are 135" — only a claim when its paragraph is about migrations; alone it means
#: nothing, which is why this one is resolved against the PARAGRAPH and the form above is
#: not. `D11.1` splits exactly this way: the subject in one sentence, the number in the next.
_THERE_ARE = re.compile(r"there are \*{0,2}(\d{2,4})\*{0,2}", re.I)
#: "highest `0139`" — the other number the directory owns.
_HIGHEST = re.compile(r"highest\s+`?(\d{4})`?", re.I)
_MIGRATION_SUBJECT = re.compile(r"\bmigrations\b", re.I)


def _migration_owner() -> tuple[str, str]:
    """(count, highest id) read from the directory that owns them."""
    names = sorted(p.name for p in _MIGRATIONS.glob("*.sql"))
    return str(len(names)), names[-1][:4]


def _migration_claims(text: str) -> list[tuple[int, str, set[str]]]:
    """(line_no, sentence, claimed numbers) for every migration COUNT claim in `text`.

    ONE PREDICATE, asked by the corpus sweep and by the pinned-shape tests. It is factored
    because I wrote this file's other shared predicate for exactly this reason and then
    made the same mistake again twenty minutes later: a test that reimplemented the
    paragraph rule locally stayed green while the real function had it mutated away. Two
    copies of one rule, in the file whose subject is two copies of one rule.
    """
    out: list[tuple[int, str, set[str]]] = []
    block: list[str] = []
    start = 1
    blocks: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            if not block:
                start = i
            block.append(line)
        else:
            if block:
                blocks.append((start, " ".join(x.strip() for x in block)))
            block = []
    if block:
        blocks.append((start, " ".join(x.strip() for x in block)))
    for line_no, joined in blocks:
        # THE ONE PLACE THIS FILE WIDENS SCOPE RATHER THAN NARROWING IT. "There are 135"
        # means nothing on its own, so it is a claim only when its PARAGRAPH is about
        # migrations — which is how D11.1's number was caught, the subject being in the
        # previous sentence. The "138 migrations" form is self-contained and needs none of
        # this; the asymmetry is in the shape of the claim, not in the prose.
        about = bool(_MIGRATION_SUBJECT.search(joined))
        for sentence in _SENTENCE.split(joined):
            claimed = set(_MIGRATION_COUNT.findall(sentence))
            if about:
                claimed |= set(_THERE_ARE.findall(sentence))
                claimed |= set(_HIGHEST.findall(sentence))
            if claimed:
                out.append((line_no, sentence, claimed))
    return out


def _migration_disagreements() -> tuple[int, list[str]]:
    count, highest = _migration_owner()
    seen = 0
    bad: list[str] = []
    for path in _tracked_markdown():
        rel = str(path.relative_to(_ROOT))
        for line_no, sentence, claimed in _migration_claims(
            path.read_text(encoding="utf-8", errors="ignore")
        ):
            seen += 1
            if _DATED.search(sentence):
                continue
            wrong = sorted(claimed - {count, highest})
            if wrong:
                bad.append(f"{rel}:~{line_no} states {wrong} — {sentence[:120]}")
    return seen, bad


@pytest.mark.tripwire
def test_no_document_states_a_migration_count_the_directory_does_not_have() -> None:
    seen, bad = _migration_disagreements()
    count, highest = _migration_owner()
    assert not bad, (
        f"{len(bad)} migration-count claim(s) disagree with the directory "
        f"({count} files, highest {highest}). Do not restate a number the tree owns — "
        "point at it, or date the sentence if it is a record:\n  " + "\n  ".join(bad)
    )
    assert seen >= 3, f"only {seen} migration-count claims seen — the rule has gone blind"


@pytest.mark.tripwire
def test_a_specific_migration_id_is_not_read_as_a_count() -> None:
    """`migration 0110` is a reference, not a claim about how many there are.

    Measured: without the position rule, 14 sentences flag and nearly all are legitimate
    id references. A guard that fires on correct work is the failure this repo pays for.
    """
    for sentence in (
        "Migration 0110 removed a column for the same reason.",
        "It was first written as migration `0137`, and the first behavioural test failed.",
        "`skills` table (migration 0107) | the curator moves it",
    ):
        assert not _MIGRATION_COUNT.findall(sentence), sentence


@pytest.mark.tripwire
def test_it_catches_the_migration_counts_that_were_wrong() -> None:
    """The five real sites, verbatim. 138 files, highest `0139`."""
    count, highest = "138", "0139"
    for sentence in (
        "**StackOwl.** SQLite with pool + 90 migrations, `conversations`/`messages`.",
        "There are **136** migrations, and the cost is not one replay per test.",
        "there are **136** migrations, not 90 (all applied, highest 0136);",
        "**The map said 90 migrations.",
    ):
        claimed = set(_MIGRATION_COUNT.findall(sentence)) | set(_HIGHEST.findall(sentence))
        assert claimed - {count, highest}, f"not flagged: {sentence!r}"


@pytest.mark.tripwire
def test_the_migration_owner_reads_the_directory_rather_than_a_number() -> None:
    """Kills the hardcode, which the corpus sweep cannot.

    A clean corpus and a guard reading the WRONG owner look identical from outside — the
    exact failure DEBT-208 was about, recurring inside the guard written for it. Mutating
    `_migration_owner` to return a fixed 136 left all sixteen tests green, because no
    document states a count any more. So this asserts the RELATION to the directory, by a
    different route than the function takes, and never the value — the count is free to
    grow and must.
    """
    count, highest = _migration_owner()
    on_disk = [f for f in _MIGRATIONS.iterdir() if f.suffix == ".sql"]
    assert int(count) == len(on_disk), f"{count} claimed, {len(on_disk)} .sql files present"
    assert any(f.name.startswith(highest) for f in on_disk), (
        f"highest id {highest!r} names no file in {_MIGRATIONS}"
    )


@pytest.mark.tripwire
def test_a_count_in_a_following_sentence_is_still_a_claim() -> None:
    """The D11.1 shape, and the only test that keeps the paragraph subject alive.

    `**The map said 90 migrations. There are 135.** Corrected.` splits into two sentences:
    the subject is in the first, the number in the second. "There are 135" alone means
    nothing, so it is resolved against the PARAGRAPH — the one place this file deliberately
    widens scope rather than narrowing it, and it is why 135 was caught at all. Mutating
    that away left every other test green.
    """
    doc = "**The map said 90 migrations. There are 135.** Corrected.\n"
    found: set[str] = set()
    for _, _, claimed in _migration_claims(doc):
        found |= claimed
    assert "135" in found, f"the following-sentence count was not seen: {sorted(found)}"
    assert "90" in found, f"the in-sentence count was not seen: {sorted(found)}"


@pytest.mark.tripwire
def test_the_gate_still_states_both_baselines_as_numbers() -> None:
    """The owner must remain parseable, or the guard above silently has nothing to compare.

    Asserts the CAPABILITY — two integer ceilings recoverable from the enforcing lines —
    never the values, which are free to fall.
    """
    gate = _gate_numbers()
    assert len(gate) == 2, f"expected two enforced baselines, parsed {sorted(gate)}"
    assert all(n.isdigit() for n in gate), gate
