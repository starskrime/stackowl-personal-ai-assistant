#!/usr/bin/env python3
"""Re-run every partial stage's closing check and say which ones can now close.

WHY THIS EXISTS. `escalation_check.py` was written because "an escalation got written
once, with a measurement, and was never looked at again — so its premise aged silently
while it sat in a queue". Every escalation therefore carries a `premise_check` printing
HOLDS or EXPIRED, and the loop runs them all on the way in.

VALIDATE STAGES GOT THE SAME DISCIPLINE IN PROSE AND NEVER IN CODE. `item-loop/SKILL.md`
states the rule — "a check you could not evidence stays OPEN with the query that would
close it" — and MEASURED 2026-09-06 it had not held: ten stages were `partial`, all of
them `validate`, and NOT ONE carried a runnable field. Three had a closing query written
in English inside `changes:`, where nothing could execute it. The other seven had nothing
at all, which made them dead ends rather than open questions: no later pass could close
them, because no one had recorded what closing would look like.

The consequence was quiet and expensive. Ten items sat at 6/7 indefinitely, and the only
thing that could ever advance them was a person remembering, unprompted, to re-derive a
query that mostly did not exist. That is the same "write with no reader" this programme
keeps finding, sitting in its own state of record.

D11.3 is the sharpest case and the reason `progress_lint` now REFUSES a partial without a
check. Its evidence was a frame rendered as text back to the model, logged nowhere at all,
so no volume of production traffic could ever have closed it — D08.1's DEBUG-only evidence
line one step worse. Being forced to write the check is what exposed it; an INFO line was
added in the same change, and only then did the claim become one that reality could settle.

Usage:  uv run python scripts/validate_check.py
Exit status is always 0 — this reports, it does not gate. `progress_lint.py` is the gate,
and it enforces only that a check EXISTS, never what it returns.
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import prose_closing_promises  # noqa: E402
_STAGES = (
    "brainstorm", "architect", "implement", "cleanup", "test", "validate", "document",
)


#: `log_since.sh`'s own warning, matched on its distinctive phrase rather than on an exit
#: status the script deliberately does not set — it prints the count and returns 0 so a
#: caller can use it directly, which is right and is also why the signal has to be read
#: out of stderr.
_WINDOW_TRUNCATED = "WINDOW TRUNCATED"

#: A closing check that bounds its evidence to the day a fix shipped.
_SCOPED = re.compile(r"log_since\.sh\s+(\d{4}-\d{2}-\d{2})")

#: Package-manager chatter every `uv run` writes to stderr. Not a verdict.
_UV_NOISE = re.compile(r"^\s*(?:Installed|Uninstalled|Resolved|Audited|Downloading|Built)\b.*$",
                       re.M)

#: How many days of warning is enough to act. Rotation drops one file per day, so this is
#: literally "days remaining", and three is the smallest number that survives a weekend.
_ROT_WARNING_DAYS = 3


def verdict_of(line: str, truncated: bool) -> str:
    """CLOSEABLE | EXPIRED | open | ????? for one check's last output line.

    EXPIRED IS A THIRD STATE, and collapsing it into `open` is the defect this fixed. A
    date-bounded check asks "has this happened SINCE the fix shipped?". Once the logs no
    longer reach that date the honest answer is "this question can no longer be asked" —
    but the check still prints `OPEN (0)`, and OPEN reads as *not yet*. The item then sits
    at 6 of 7 forever with a mechanism that cannot advance it, which is precisely the dead
    end `closing_check` was built to end.

    BOTH CONDITIONS ARE REQUIRED, and the first version of this had only the truncation.
    `log_since.sh` warns whenever the requested date predates the oldest retained log
    REGARDLESS of the count, so a check whose evidence still survives inside the shortened
    window returns a real hit AND a warning together. Marking on the warning alone would
    have relabelled true CLOSEABLEs as EXPIRED — worse than the defect being fixed, because
    it hides an item that was ready to close. Only the ZERO is ambiguous.

    A function rather than a branch inside `main()` so the tests can ask the real
    predicate. Writing the decision twice is this repo's most repeated defect, and it was
    committed twice in one session last loop inside the guard written against it.
    """
    if line.startswith("CLOSEABLE"):
        return "CLOSEABLE"
    if line.startswith("OPEN"):
        return "EXPIRED" if truncated else "open"
    return "?????"


def _logs_dir() -> Path | None:
    """The log directory, asked of `StackowlHome` rather than re-derived.

    D18.3 — one source for the home. A script that rebuilds `~/.stackowl` itself reports
    on a different instance than the process it is asking about, and `log_since.sh`
    already resolves it exactly this way.
    """
    try:
        out = subprocess.run(
            ["uv", "run", "python", "-c",
             "from stackowl.paths import StackowlHome; print(StackowlHome.logs_dir())"],
            cwd=_ROOT, capture_output=True, text=True, timeout=120,
        )
        logs = Path((out.stdout or "").strip())
    except Exception as exc:  # noqa: BLE001 — degrade to silence, and say why
        print(f"  (rot clock unavailable: {exc})")
        return None
    return logs if logs.is_dir() else None


def _retention() -> tuple[str, int, int]:
    """(oldest retained date, dated files present, files retention permits).

    ALL THREE, because the first version of this reported a countdown derived from ONE
    of them and was wrong by three weeks. It assumed the horizon slides a day per day.
    It does not: `backupCount` is 30 and only TEN dated files exist, and
    `TimedRotatingFileHandler.getFilesToDelete()` deletes nothing until the count
    EXCEEDS that. So the horizon is FROZEN until roughly twenty more midnights have
    passed, and the clock said "2 days, ACT NOW" about a check with about 23.
    """
    logs = _logs_dir()
    if logs is None:
        return "", 0, 0
    stamped = sorted(p.name for p in logs.glob("stackowl-*.jsonl"))
    oldest = stamped[0][len("stackowl-"):-len(".jsonl")] if stamped else ""
    try:
        import os
        permitted = int(os.environ.get("STACKOWL_LOG_RETAIN_DAYS", "30"))
    except ValueError:
        permitted = 30
    return oldest, len(stamped), permitted


def _report_rot_clock(items: list[dict]) -> None:
    """Say when each date-bounded check stops being answerable, and show the working.

    THE PREVENTIVE HALF. EXPIRED tells you a check has died; this tells you it is going
    to, which is the only moment at which re-keying it is cheap.

    THE FIRST VERSION OF THIS WAS WRONG, and wrong in the most dangerous direction — it
    printed a confident, precise, three-weeks-early countdown. It computed
    `bound - oldest_retained`, which is the number of days of history the check currently
    has BEHIND its bound; that is not a countdown to anything. Rotation only deletes once
    the dated files EXCEED `backupCount`, and there are ten against a permitted thirty, so
    nothing is being deleted at all yet. Measured: it reported "2d ACT NOW" for D03.2,
    whose bound actually survives until about 2026-09-30.

    So the projection is stated WITH its inputs rather than on its own. That is not
    decoration either: `observability.py` records that five dated files vanished in one
    night on 2026-08-30 and says in as many words that THE CAUSE OF THAT DELETION IS
    UNPROVEN. A day-counter over a sensor known to have failed once is a precision
    instrument on a broken gauge, so the honest output shows the gauge — ten files of a
    permitted thirty — beside the projection that assumes it behaves.
    """
    oldest, present, permitted = _retention()
    if not oldest:
        print("\nROT CLOCK — unavailable: no dated log files, so nothing can be timed.")
        return
    today = datetime.date.today()
    rows: list[tuple[datetime.date, str, str]] = []
    for item in items:
        found = _SCOPED.findall(str(item.get("closing_check") or ""))
        if found:
            bound = datetime.date.fromisoformat(min(found))
            # A bound expires the day the oldest retained log passes it. Deletion starts
            # only once the dated files exceed `permitted`, and from then the oldest is
            # `today - permitted`, so `bound` is lost on `bound + permitted + 1`.
            rows.append((bound + datetime.timedelta(days=permitted + 1),
                         str(item.get("id")), min(found)))
    header = (f"\nROT CLOCK — retained window starts {oldest}; {present} dated file(s) "
              f"present of a permitted {permitted}, so nothing is being deleted "
              f"{'yet' if present <= permitted else 'any longer'}.")
    if not rows:
        print(f"{header} No check bounds its evidence to a date, across {len(items)} "
              "check(s). (The denominator is printed because a silent clock and a corpus "
              "with nothing to time look identical.)")
        return
    rows.sort()
    print(f"{header} Projected expiry below ASSUMES retention behaves as configured — it "
          f"did not on 2026-08-30, when five dated files vanished in one night for a "
          f"reason `observability.py` still records as UNPROVEN. Re-key a check onto "
          f"something that does not rot before its date, not after:")
    urgent = [r for r in rows if (r[0] - today).days <= _ROT_WARNING_DAYS]
    for when, ident, bound in rows[:12]:
        days = (when - today).days
        note = "  <-- ACT NOW" if days <= _ROT_WARNING_DAYS else ""
        print(f"  {days:>4}d  {ident:<14} bound {bound} -> unanswerable ~{when}{note}")
    if len(rows) > 12:
        print(f"  … and {len(rows) - 12} more, all later")
    print(f"  {len(urgent)} of {len(rows)} within {_ROT_WARNING_DAYS} days.")


def main() -> int:
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    items = [
        item for item in data.get("items", [])
        if any((item.get("stages") or {}).get(s) == "partial" for s in _STAGES)
    ]
    # Evidence-led work lives in `known_debt`, not in `items`, so its claims used to
    # have no re-runnable check at all — the dead end DEBT-124 removed for items,
    # one population over. A debt carrying a `closing_check` is now re-run beside
    # them, labelled by its own id.
    # A DEBT'S OWN STAGE IS READ, NOT FABRICATED. This wrote
    # `stages={"validate": "partial"}` unconditionally, so a debt whose validate was
    # recorded `done` stayed in the report FOREVER — and, once its evidence arrived,
    # as a permanent CLOSEABLE that no action could ever clear. MEASURED 2026-09-07
    # while closing DEBT-153: the moment its stage became `done` the report still
    # called it a partial validate needing attention. A report that cannot tell a
    # closed claim from an open one teaches its reader to skim it, which is the exact
    # failure the prose-promise report was built to avoid.
    #
    # The check itself is NOT discarded — it stays in the record as the evidence the
    # close rests on, and other guards still re-run it. It simply stops being listed
    # as outstanding work.
    items += [
        dict(d, stages={"validate": (d.get("stages") or {}).get("validate", "partial")})
        for d in (data.get("known_debt", []) or [])
        if (d.get("closing_check") or "").strip()
        and (d.get("stages") or {}).get("validate", "partial") == "partial"
    ]

    # AND `current`, WHICH WAS THE THIRD SECTION AND THE LAST TO GET THE CURE.
    #
    # The dead end this script exists to remove — a partial stage whose closing query
    # nothing can execute — was fixed for `items`, then extended to `known_debt` (the
    # block above says so in as many words). `current` was never added, so an
    # evidence-led item recorded there could carry a perfectly good executable check
    # that NOTHING WOULD EVER RUN. MEASURED 2026-09-07 the moment one of my own records
    # landed there: FIVE entries, all `validate: partial`, all with a runnable check, all
    # invisible — four of them written earlier the same day by other loops.
    #
    # `current` is a MAPPING keyed by record name, not a list, so the id has to come from
    # the key; every other section carries its own `id`.
    # ANY PARTIAL STAGE, not just `validate` — this module's own first line says it
    # re-runs "every partial stage's closing check", and reading only `validate` made
    # that false. MEASURED 2026-09-08 the moment an incident was recorded with
    # `brainstorm: partial` (its diagnosis genuinely incomplete) and a runnable check:
    # the check was invisible, which is the same dead end this file exists to remove,
    # one AXIS over rather than one population over. The mapped-items selection above
    # already reads any stage; only the two later populations hardcoded one.
    items += [
        dict(rec, id=name, stages=dict(rec.get("stages") or {}))
        for name, rec in (data.get("current") or {}).items()
        if isinstance(rec, dict)
        and (rec.get("closing_check") or "").strip()
        and any((rec.get("stages") or {}).get(st) == "partial" for st in _STAGES)
    ]

    print(f"partial stages: {len(items)} item(s)\n")
    closeable: list[str] = []
    unverifiable: list[str] = []
    expired: list[str] = []

    for item in sorted(items, key=lambda i: str(i.get("id"))):
        ident = str(item.get("id"))
        stages = [s for s in _STAGES if (item.get("stages") or {}).get(s) == "partial"]
        check = (item.get("closing_check") or "").strip()
        if not check:
            # progress_lint refuses this, so it can only appear on an unlinted edit.
            unverifiable.append(ident)
            print(f"  ????     {ident} ({', '.join(stages)}) — NO closing_check")
            continue
        truncated = False
        try:
            out = subprocess.run(
                check, shell=True, cwd=_ROOT, capture_output=True,
                text=True, timeout=180,
            )
            # STDERR IS A SIGNAL, NOT A FALLBACK — this line used to read
            # `(out.stdout or out.stderr or "")`, so stderr was consulted ONLY when
            # stdout was empty. Every check echoes CLOSEABLE/OPEN to stdout, so stdout
            # was never empty, so stderr was discarded on 100% of runs. What it was
            # discarding is `log_since.sh`'s WINDOW TRUNCATED warning — a warning that
            # script's own comments say is sent to stderr *"rather than being silently
            # absorbed"*, absorbed by its only caller. Built, documented, and not wired.
            truncated = _WINDOW_TRUNCATED in (out.stderr or "")
            # THE FALLBACK MUST NOT REPORT TOOLING NOISE AS A VERDICT. `uv run` writes
            # "Installed 1 package in 7ms" to stderr on every invocation, and several
            # checks shell out through it, so a check that printed nothing to stdout used
            # to have that line reported as its answer. Filtered rather than dropped,
            # because a real traceback on stderr IS the most useful thing to show.
            noise = (_UV_NOISE.sub("", out.stderr or "")).strip()
            verdict = ((out.stdout or "").strip() or noise).splitlines()
            line = verdict[-1] if verdict else "<no output>"
        except subprocess.TimeoutExpired:
            line = "<check timed out>"
        except Exception as exc:  # noqa: BLE001 — the message is the output
            line = f"<check raised: {exc}>"

        # The check names its own verdict; this never infers one from an exit code.
        # A check that says nothing is reported as saying nothing, because a silent
        # instrument reading as "still open" is how the prose versions of these
        # queries went unread for a month.
        mark = verdict_of(line, truncated)
        if mark == "CLOSEABLE":
            closeable.append(ident)
        elif mark == "EXPIRED":
            expired.append(ident)
            line = f"{line[:110]}  [WINDOW TRUNCATED — re-key onto evidence that does not rot]"
        elif mark == "?????":
            unverifiable.append(ident)
        print(f"  {mark:<9} {ident} ({', '.join(stages)}) — {line[:170]}")

    print(
        f"\nchecked {len(items)}, CLOSEABLE {len(closeable)}, "
        f"EXPIRED {len(expired)}, unverifiable {len(unverifiable)}"
    )
    _report_rot_clock(items)
    _report_prose_promises(data)
    if closeable:
        print(
            "\nThese have the evidence they were waiting for. Re-read the item, confirm "
            "the check measures what the stage actually claimed, and advance it:\n  "
            + "\n  ".join(closeable)
        )
    return 0


def _report_prose_promises(data: dict) -> None:
    """Name every closing query written where nothing can execute it.

    THE SAME DEAD END THIS FILE ALREADY REMOVED, one property over. The loop above
    re-runs checks belonging to a `partial` stage or to a debt that already carries a
    `closing_check`. Neither condition describes what a closing query IS FOR: a claim
    whose evidence has not arrived yet. A record can be `done` or `no_change_needed`
    and still rest on evidence its own author called provisional — and then no
    enumeration reaches it, so the promise is never kept.

    DEBT-105 is the archetype and it cost something measurable. Recorded
    `no_change_needed` with "Closing query: re-run the same before/after split over a
    full day of traffic on 2026-09-02", it sat four days. Run on 2026-09-06 it did not
    overturn the decision, but it corrected the number: the recorded "77% reduction"
    had compared telegram AFTER against the all-channel BEFORE. Like for like it is 39%.

    These cannot be executed — they are prose, and several wait on traffic that has not
    happened. They can be NAMED, every loop, which is the part that was missing.
    """
    promises = prose_closing_promises(data)
    if not promises:
        return
    records = sorted({record for record, _field, _text in promises})
    print(
        f"\nPROSE PROMISES: {len(promises)} closing quer(ies) in {len(records)} "
        "record(s), written where nothing can run them.\nThese are invisible to the "
        "checks above. Convert one to a `closing_check` when you touch its record:"
    )
    for record, field, text in promises:
        print(f"  {record:<46} {field.split('.')[-1][:26]:<26} {text[:60]}")


if __name__ == "__main__":
    sys.exit(main())
