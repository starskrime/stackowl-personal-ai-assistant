"""`Reviewed:` dismisses ONE named commit — it can never become a silencer.

The header field exists because `Last verified: <date>, against commit <sha>`
conflates two facts: WHEN a document's claims were checked, and WHICH TREE they
were checked against. An unrelated commit to a cited file moves the second and
leaves the first alone, so neither available answer was honest — re-running a
whole Verification section proves nothing new, and bumping the date asserts a
re-run that did not happen.

MEASURED 2026-09-08: FIVE documents went stale on ONE commit, `1f48d999`, a
scheduler-drain fix, because all five cite `startup/orchestrator.py` — 4,839
lines wiring the whole platform, changed in 16 commits since 2026-09-01. Four of
the five cite it only as the place their subject is WIRED. Making the truthful
response expensive is how documents rot; nobody decides to lie.

THIS FILE IS THE CEILING, and it is why the opt-out is safe to have. This repo
has already paid for an opt-out with no ceiling, so every property below closes a
specific way of turning a dismissal into a blanket:

* the sha must be a REAL commit — not a plausible-looking string;
* it must actually TOUCH a source the document cites — so an unrelated sha
  cannot be pasted in to quiet the report;
* it must be NEWER than the document's `Last verified` date — an older commit
  was already covered by that verification, so listing one buys nothing and
  hides the fact that nothing was examined;
* it must carry a REASON in prose — a bare sha records that someone looked, not
  what they concluded, and the conclusion is the whole artefact.

None of these can be satisfied by a future change: an entry names a commit that
already exists, so it expires by construction the moment anything else lands.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _doc_check():
    """Import `scripts/doc_check.py` and ASK IT for its own definitions.

    Restating the header parser or the sha regex here would be two copies of one
    rule — the shape this repo pays for most. If the detector's idea of a
    `Reviewed:` entry moves, this guard moves with it or it starts guarding
    something else.
    """
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_reviewed", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_reviewed"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entries() -> list[tuple[str, dict[str, str], set[str]]]:
    """(document, header, reviewed shas) for every document carrying the field."""
    mod = _doc_check()
    out = []
    for doc in sorted(_DESIGNS.glob("*.md")):
        head = mod._header(doc.read_text(encoding="utf-8"))  # noqa: SLF001
        shas = mod._reviewed_shas(head)  # noqa: SLF001
        if shas:
            out.append((doc.name, head, shas))
    return out


@pytest.mark.tripwire
def test_every_reviewed_sha_is_a_commit_that_touched_a_cited_source() -> None:
    """The two ways a dismissal could be fiction: the commit, and the connection."""
    mod = _doc_check()
    offenders: list[str] = []
    for name, head, shas in _entries():
        source = mod._source_fields(head)  # noqa: SLF001
        cited = [p for p in re.findall(mod._CITATION, source) if "/" in p]  # noqa: SLF001
        real = [r for p in cited if (r := mod._resolve(p))]  # noqa: SLF001
        for sha in sorted(shas):
            touched = subprocess.run(
                ["git", "show", "--format=", "--name-only", sha, "--", *real],
                capture_output=True, text=True, timeout=60, cwd=_ROOT,
            )
            if touched.returncode != 0:
                offenders.append(f"{name}: `{sha}` is not a commit in this repository")
            elif not touched.stdout.strip():
                offenders.append(
                    f"{name}: `{sha}` is a real commit but touches none of the "
                    f"{len(real)} sources this document cites"
                )
    assert not offenders, (
        "a `Reviewed:` entry dismisses a specific change to a specific cited "
        "source. These entries dismiss nothing, so the document is reading as "
        "reviewed while nothing was examined:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.tripwire
def test_a_reviewed_commit_is_newer_than_the_verification_it_supplements() -> None:
    """An older commit was already inside the verification, so listing it is noise
    that looks like diligence — and the report would count the document as
    reviewed-not-rerun on the strength of it."""
    offenders: list[str] = []
    for name, head, shas in _entries():
        date = re.search(r"(\d{4}-\d{2}-\d{2})", head.get("Last verified", ""))
        if not date:
            offenders.append(f"{name}: carries `Reviewed:` with no dated `Last verified`")
            continue
        for sha in sorted(shas):
            when = subprocess.run(
                ["git", "show", "-s", "--format=%cs", sha],
                capture_output=True, text=True, timeout=60, cwd=_ROOT,
            ).stdout.strip()
            if when and when <= date.group(1):
                offenders.append(
                    f"{name}: `{sha}` landed {when}, on or before the document was "
                    f"verified ({date.group(1)}) — it was already covered"
                )
    assert not offenders, "\n  ".join(["stale `Reviewed:` entries:", *offenders])


@pytest.mark.tripwire
def test_every_reviewed_commit_records_WHY_it_does_not_apply() -> None:
    """A bare sha says somebody looked. The conclusion is the artefact."""
    for name, head, shas in _entries():
        text = head.get("Reviewed", "")
        prose = re.sub(r"`[0-9a-f]{7,40}`", " ", text)
        words = [w for w in re.findall(r"[A-Za-z]{3,}", prose)]
        assert len(words) >= 8 * len(shas), (
            f"{name}: `Reviewed:` lists {len(shas)} commit(s) and {len(words)} words "
            f"of reason. Record WHY the change does not touch this document's claims "
            f"— the next reader has to be able to disagree with it."
        )


@pytest.mark.tripwire
def test_a_reviewed_field_that_names_no_commit_is_not_a_dismissal() -> None:
    """THE VACUITY THE OTHER THREE CANNOT SEE, because `_entries()` drops it.

    `_reviewed_shas` extracts backticked hex only, and `_entries()` above keeps a
    document only `if shas`. So a `Reviewed:` field naming something that is not a
    commit yields the EMPTY SET and vanishes from every guard in this file: the
    document reads as reviewed to a person, counts as unreviewed to `doc_check`,
    and is checked by nothing. A zero numerator over a zero denominator is not a
    pass — this repo's own rule, applied to its own guard.

    MEASURED 2026-09-09: 22 documents carry the field and TWO yielded nothing —
    D07.3 said `DEBT-261` and D14.4 said `DEBT-263`, naming the ITEM that made the
    change rather than the COMMIT that made it. Both were written by the loops that
    shipped those items, and both left their document in the STALE list while
    looking answered. Writing the item id is the natural mistake: it is what the
    author has in mind, and nothing said the field is machine-read.
    """
    mod = _doc_check()
    offenders: list[str] = []
    for doc in sorted(_DESIGNS.glob("*.md")):
        head = mod._header(doc.read_text(encoding="utf-8"))  # noqa: SLF001
        if "Reviewed" not in head:
            continue
        if not mod._reviewed_shas(head):  # noqa: SLF001
            offenders.append(f"{doc.name}: {head['Reviewed'][:70]!r}")
    assert not offenders, (
        "these documents carry a `Reviewed:` field that yields NO commit sha, so it "
        "dismisses nothing and every other guard in this file skips them — write the "
        "backticked short sha (an item id is not a commit):\n  " + "\n  ".join(offenders)
    )
