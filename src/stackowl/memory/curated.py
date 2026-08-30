"""Curated memory — the two files that are always in the prompt (D08.1).

WHAT THIS REPLACES, and why it is smaller than what it replaces.

Measured on the live database 2026-08-08: 88,631 committed facts, 9.75M chars.
37.1% mentioned a trace id or ``failure_class`` — the platform's own diagnostics
stored as durable memory about the user. 49.1% had never been reinforced. The
single most-reinforced entry in the whole store was ``"Today's date is
2026-07-15"``, three weeks stale, reinforced 157 times, because
``reinforcement_count`` measured how often a topic came up rather than whether it
mattered.

Meanwhile ``load_user_profile()`` ran on every prompt build against a file that
did not exist and that nothing wrote. D01.1 removed per-turn recall from the
prompt for a good reason (Law 1) and replaced it with a reader with no writer, so
the system prompt carried NO curated memory at all.

This module is the writer. Two files, a hard budget, and the agent doing its own
forgetting.

THE FOUR RULES, each earned:

  * **A hard budget, and the agent consolidates.** At capacity ``add`` REFUSES
    and tells the model to merge or remove and retry in this turn. Forgetting
    becomes a visible in-turn problem rather than a background process nobody
    watches — which is exactly what the 88k store lacked.
  * **Durability is stated and stored.** Every entry is ``permanent`` or
    ``until_changed``; ``transient`` is not accepted, so a stale date has nowhere
    to go. Storing it (not just checking it) lets eviction prefer
    ``until_changed`` over ``permanent`` instead of evicting by age — age is what
    let 43,503 unreinforced facts survive.
  * **The prompt reads a FROZEN SNAPSHOT.** Live state is what the tool reports;
    the snapshot is what the prompt carries, and it does not move mid-session.
    An unstable prompt forfeits the provider's prefix cache with no marker to
    blame (measured in D01.1).
  * **A failed memory write must never cost the user their reply.** Consolidation
    failures are bounded per turn; past the cap the response goes terminal so the
    model stops trying and answers the question.

DIVERGENCE FROM THE REFERENCE PLATFORM, stated: agent notes are PER-OWL
(``<owl>.md``) because our lanes already are (``owl:secretary:telegram:dm:…``)
and owls have their own DNA. Theirs has one agent and one notes file. The user
profile stays global — the user is the same person to every owl.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

__all__ = [
    "DURABILITIES",
    "OWL_BUDGET_CHARS",
    "USER_BUDGET_CHARS",
    "USER_TARGET",
    "CuratedMemory",
    "MemoryResult",
    "NUDGE_INTERVAL_TURNS",
    "memory_dir",
    "note_turn",
    "note_write",
    "reset_nudges",
    "shared_memory",
]

#: Separates entries within a file. Adopted from the reference platform: a bare
#: sentinel on its own line survives a user hand-editing the file in a way that
#: markdown headings or YAML would not.
ENTRY_DELIMITER = "\n§\n"

#: Adopted as-is from the reference platform (D08.1 R2Q7), the same call as
#: D10.2's 60-character description cap. Small enough that adding forces
#: dropping, which is the entire mechanism.
USER_BUDGET_CHARS = 1375
OWL_BUDGET_CHARS = 2200

#: The largest share of a target's budget ONE entry may claim (D08.4). Without
#: this a single write could evict the whole store: measured on the real class,
#: four facts went in and one 1,300-character `add` left one standing. The budget
#: is the mechanism this design rests on ("adding forces dropping"), and a write
#: able to consume all of it turns that mechanism into a memory-wipe primitive
#: available to anything that can write once. A fraction rather than a constant so
#: it cannot drift out of step with the budgets above — one source, not three.
#:
#: 0.5 is CALIBRATED, not picked: measured against the live store on 2026-08-22, the
#: largest real entry is 878 chars against a 2,200 budget — 39.9% — so a 50% ceiling
#: refuses ZERO of the 32 entries that exist, with margin. Two tests did break on it,
#: both using synthetic ~700-char probes against the smaller 1,375 `user` budget;
#: their probes are now derived from this fraction rather than hardcoded, so they
#: cannot drift out of step with it again.
MAX_ENTRY_BUDGET_FRACTION = 0.5

#: The global profile's target name. Anything else is an owl.
USER_TARGET = "user"

#: ``transient`` is deliberately ABSENT. It is not a durability we store, it is a
#: reason to refuse — the most-reinforced entry in the old store was a stale
#: date, and a system that can express "this expires" will accumulate expired
#: things. The writer must commit to one of these two or not write.
DURABILITIES: tuple[str, ...] = ("permanent", "until_changed")

#: How many consecutive at-capacity failures one turn may take before the
#: response goes terminal. Without a cap a fragile consolidation can loop the
#: turn to budget exhaustion and suppress the user's reply entirely.
MAX_CONSOLIDATION_FAILURES_PER_TURN = 3

#: ``[permanent] the text`` — the durability rides in the entry so eviction can
#: sort on it. Tolerant of a hand-edited file that has lost the marker.
_DURABILITY_RE = re.compile(r"^\[(permanent|until_changed)\]\s*(.*)$", re.DOTALL)

#: A file name we are willing to create. Owl names reach this from a registry,
#: but the path is built from them, so it is validated rather than trusted.
#: Word tokenizer for subject inference. Unicode-aware by construction (`\w`
#: with re.UNICODE), because owl names and facts are not English-only — the
#: standing rule here is that keyword logic must never be an English word list.
_WORD_RE = re.compile(r"\w+", re.UNICODE)

_SAFE_TARGET_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def memory_dir() -> Path:
    """Where both files live. Voted in D08.1 R7Q26."""
    return StackowlHome.home() / "memory"


#: One instance per process, because the frozen snapshot is per-process state
#: and a fresh instance per turn would defeat it. Mirrors how the prompt store
#: is held in the assemble step.
_SHARED: CuratedMemory | None = None


#: How many conversations' frozen snapshots are retained. Bounded for the same
#: reason TurnCostLedger and PlanStore are: a per-conversation map with no ceiling
#: leaks for the life of the process. MRU-evicting, so the conversation currently
#: running is never the one dropped — evicting it would restore the exact bug this
#: replaced.
_MAX_TRACKED_CONVERSATIONS = 64


def shared_memory() -> CuratedMemory:
    """The process-wide curated memory.

    Writes are stateless so a caller may construct its own; the SNAPSHOT is not,
    so anything building a system prompt must come through here or it will
    re-read the file mid-session and move the prompt underneath itself.
    """
    global _SHARED  # noqa: PLW0603 — one process-wide snapshot, deliberately
    if _SHARED is None:
        _SHARED = CuratedMemory()
    return _SHARED


@dataclass(frozen=True)
class Entry:
    """One remembered thing."""

    text: str
    durability: str = "permanent"

    def rendered(self) -> str:
        return f"[{self.durability}] {self.text}"

    @classmethod
    def parse(cls, raw: str) -> Entry:
        """Read an entry back, tolerating a hand-edited file.

        A user who deletes the ``[permanent]`` marker while editing has written
        a perfectly reasonable line; treating that as corruption would punish
        exactly the editing this design exists to allow. Unmarked reads as
        ``permanent`` — the conservative default, since permanence only makes
        eviction less eager.
        """
        match = _DURABILITY_RE.match(raw.strip())
        if match is None:
            return cls(text=raw.strip())
        return cls(text=match.group(2).strip(), durability=match.group(1))


@dataclass
class MemoryResult:
    """What a write did — returned to the model, so it has to be actionable.

    ``done`` is the terminal flag. A successful write sets it and the response
    deliberately does NOT echo the entry list: the reference platform observed
    the model treating an echoed list as an invitation to "find more to fix" and
    re-issuing the same operations five times. Entries are echoed only on the
    over-capacity path, where the model genuinely needs them to choose what to
    consolidate.
    """

    ok: bool
    message: str
    done: bool = True
    target: str = ""
    usage: str = ""
    entry_count: int = 0
    entries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "success": self.ok,
            "done": self.done,
            "target": self.target,
            "usage": self.usage,
            "entry_count": self.entry_count,
            "message": self.message,
        }
        if self.entries:
            out["current_entries"] = self.entries
        return out


class CuratedMemory:
    """The two curated files, their budgets, and the consolidation protocol.

    One instance per process. Stateless apart from the frozen snapshot and the
    per-turn failure counter, so it can be constructed freely in tests.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or memory_dir()
        #: Frozen at :meth:`snapshot_for_prompt`'s first call per session, never
        #: mid-session. See the module docstring's third rule.
        # PER-CONVERSATION, bounded, MRU-evicting. This was a SINGLE slot
        # (``_snapshot`` + ``_snapshot_key``), which froze the profile only while
        # ONE conversation was running: a turn in conversation B evicted A's
        # snapshot, and A's next turn re-read the file and moved its own prompt.
        # MEASURED 2026-08-30: 89 of 259 `prompt part CHANGED` cache-invalidation
        # warnings name the `profile` part (jobmarket 66, secretary 30). This
        # platform serves chats concurrently by design, so the single slot could
        # not deliver the freeze assemble.py depends on.
        self._snapshots: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._consolidation_failures = 0

    # ------------------------------------------------------------------ paths

    def path_for(self, target: str) -> Path:
        """The file backing ``target``. Raises on a target we will not create."""
        if target == USER_TARGET:
            return self._root / "USER.md"
        if not _SAFE_TARGET_RE.match(target):
            # Not paranoia: owl names are user-chosen and this builds a path
            # from one. A name with a separator in it would write outside the
            # memory directory.
            raise ValueError(
                f"invalid memory target {target!r} — expected 'user' or an owl name"
            )
        return self._root / f"{target}.md"

    def _describe(self, target: str) -> str:
        """How a destination is named back to the reader — the FILE, not the key.

        "USER.md" and "jobmarket.md" are what the operator sees on disk, so they
        are what a confirmation should say. `path_for` already owns the mapping;
        this asks it rather than rebuilding the filename.
        """
        try:
            return self.path_for(target).name
        except ValueError:
            return target

    def known_targets(self) -> list[str]:
        """Every target that exists RIGHT NOW — user, plus one per owl file.

        Read from disk rather than compiled in, so an owl created a minute ago
        routes correctly with no code change, and a renamed owl stops matching
        its old name. Same enumeration `search` uses; kept here so there is one
        copy of "what targets exist".
        """
        try:
            return [USER_TARGET] + sorted(
                p.stem for p in self._root.glob("*.md") if p.name != "USER.md"
            )
        except Exception as exc:  # B5 — inference must never cost the turn
            log.memory.warning(
                "[curated] known_targets: could not list — assuming user only",
                exc_info=exc, extra={"_fields": {"root": str(self._root)}},
            )
            return [USER_TARGET]

    def infer_target(self, text: str) -> str:
        """Which target is this fact ABOUT? Falls back to the user (ESC-48).

        Bakir, 2026-08-24: "Infer from the fact text", and on low confidence
        "default user, always name it".

        The inference is deliberately NOT a heuristic over language: it matches
        the fact's words against the names of owls that actually exist. That
        keeps it multilingual (no English keyword list, which is a standing rule
        here), keeps it correct across renames, and makes a wrong answer possible
        only when a real owl's name genuinely appears in a fact about something
        else.

        EXACTLY ONE match routes. Zero matches, or more than one, falls back to
        the user — two owls named is less information than one, not more, and
        guessing there is precisely the silent-misroute failure mode this design
        exists to avoid.

        The match is case-insensitive but RESOLVES TO THE EXISTING SPELLING: the
        live memory dir holds both `falcon.md` and `Falcon.md` because `path_for`
        builds the filename verbatim from whatever the model typed. Inference
        must not mint a third case.
        """
        tokens = {t.casefold() for t in _WORD_RE.findall(text or "")}
        if not tokens:
            return USER_TARGET
        hits = [
            t for t in self.known_targets()
            if t != USER_TARGET and t.casefold() in tokens
        ]
        if len(hits) != 1:
            log.memory.debug(
                "[curated] infer_target: falling back to user",
                extra={"_fields": {"n_hits": len(hits), "hits": hits}},
            )
            return USER_TARGET
        return hits[0]

    def budget_for(self, target: str) -> int:
        return USER_BUDGET_CHARS if target == USER_TARGET else OWL_BUDGET_CHARS

    def _max_entry_chars(self, target: str) -> int:
        """Ceiling for a SINGLE entry — see MAX_ENTRY_BUDGET_FRACTION."""
        return int(self.budget_for(target) * MAX_ENTRY_BUDGET_FRACTION)

    @staticmethod
    def _contain(text: str) -> str:
        """Keep one entry's content inside one entry (D08.4).

        `entries()` splits the file on ENTRY_DELIMITER, and until now `add`/
        `replace` never looked for it in content — so ONE write whose text
        contained the delimiter produced TWO entries, and the second, having no
        `[durability]` marker of its own, parsed back as `permanent`: the single
        class `_evict_to_fit` refuses to touch. A one-line forgery minted an entry
        immune to every decay path this design depends on.

        THIS NEEDS NO ATTACKER, which is why it is fixed here rather than in a
        scanner: a legitimate multi-paragraph fact that happens to carry the
        sentinel on its own line does it by accident, and nothing would report it.

        The delimiter is neutralised rather than the write refused, because a
        model writing prose that contains a rare punctuation mark has not done
        anything wrong. Structural — no word list, no language assumption.
        """
        return text.replace(ENTRY_DELIMITER, "\n").strip()

    # ------------------------------------------------------------------- read

    def entries(self, target: str) -> list[Entry]:
        """Live entries for ``target``. Absent file is an empty list, not an error."""
        path = self.path_for(target)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except Exception as exc:  # B5 — a bad read must cost context, not a reply
            log.memory.error(
                "[curated] entries: unreadable — treating as empty",
                exc_info=exc, extra={"_fields": {"path": str(path)}},
            )
            return []
        return [Entry.parse(chunk) for chunk in raw.split(ENTRY_DELIMITER) if chunk.strip()]

    def used_chars(self, target: str) -> int:
        return len(self._render(self.entries(target)))

    def search(self, query: str) -> list[tuple[str, str]]:
        """Curated entries containing ``query``, as ``(target, text)`` pairs.

        Substring, case-folded, across the user profile and every owl file. Not
        ranked, and deliberately so: the corpus is a few dozen short lines under
        a hard character budget, and scoring that against BM25 or cosine would be
        precision theatre (D08.1 R3Q10).

        Lives HERE rather than on a tool because it is a property of curated
        memory, not of whichever caller wants it. The `memory` tool's search and
        `browser_recall_url` both ask this one implementation — a second copy is
        the two-copies-of-one-rule shape this codebase keeps having to fix.

        Never raises: an unlistable directory or one unreadable file costs the
        search its results, never the caller its turn. An empty list is the
        honest answer for "nothing known".
        """
        needle = query.casefold()
        out: list[tuple[str, str]] = []
        try:
            targets = [USER_TARGET] + sorted(
                p.stem for p in self._root.glob("*.md") if p.name != "USER.md"
            )
        except Exception as exc:  # B5 — a search must not cost the turn
            log.memory.warning(
                "[curated] search: could not list targets",
                exc_info=exc, extra={"_fields": {"root": str(self._root)}},
            )
            return out
        for target in targets:
            try:
                for entry in self.entries(target):
                    if needle in entry.text.casefold():
                        out.append((target, entry.text))
            except Exception as exc:  # B5 — one bad file is not a failed search
                log.memory.warning(
                    "[curated] search: target unreadable — skipped",
                    exc_info=exc, extra={"_fields": {"target": target}},
                )
        return out

    def tracked_conversations(self) -> int:
        """How many conversations' snapshots are retained — the bound, observable."""
        return len(self._snapshots)

    def snapshot_for_prompt(self, target: str, *, conversation_id: str) -> str:
        """The text the prompt carries — FROZEN for the life of ``conversation_id``.

        Re-reads only when the incarnation changes (D08.1 R2Q6), so a write made
        this turn reaches the prompt on the next ``/new``. Mid-session stability
        is the whole point: per-turn variation was measured as the single largest
        source of prompt instability, and it forfeits the prefix cache silently.
        """
        snapshot = self._snapshots.get(conversation_id)
        if snapshot is None:
            snapshot = {}
            self._snapshots[conversation_id] = snapshot
        self._snapshots.move_to_end(conversation_id)
        while len(self._snapshots) > _MAX_TRACKED_CONVERSATIONS:
            evicted, _ = self._snapshots.popitem(last=False)
            log.memory.debug(
                "[curated] snapshot: evicted the oldest conversation (bounded)",
                extra={"_fields": {"evicted_conversation_id": evicted}},
            )
        if target not in snapshot:
            try:
                rendered = self._render(self.entries(target))
            except ValueError:
                # D08.4 — a target this store cannot resolve yields an EMPTY block,
                # never an exception. `assemble.py` wraps BOTH snapshot calls in one
                # try, so a raise on the owl block discarded the USER block that had
                # already succeeded: one owl named `сова`, `梟` or `Bakır` (the
                # operator's own name — Turkish dotless i) silently removed the
                # global user profile from every turn of its conversations.
                # READING degrades; WRITING still raises, because a silent write to
                # nowhere is how this store grew five files nothing ever read.
                log.memory.warning(
                    "[curated] snapshot: target does not resolve to a file — "
                    "empty block for it, the rest of the profile is unaffected",
                    extra={"_fields": {
                        "target": target, "conversation_id": conversation_id,
                    }},
                )
                rendered = ""
            snapshot[target] = rendered
            # INFO, not DEBUG: production runs at INFO, so a DEBUG line here is
            # a line that never exists when it is needed. Fires once per target
            # per incarnation, so the volume is a handful of records a day.
            log.memory.info(
                "[curated] snapshot: frozen",
                extra={"_fields": {
                    "target": target, "conversation_id": conversation_id,
                    "chars": len(snapshot[target]),
                }},
            )
        return snapshot[target]

    # ------------------------------------------------------------------ write

    def reset_turn(self) -> None:
        """Clear the per-turn consolidation-failure budget. Call at turn start."""
        self._consolidation_failures = 0

    def add(self, target: str, text: str, durability: str) -> MemoryResult:
        """Append an entry, or REFUSE and ask for consolidation.

        Refusing is the mechanism, not a failure mode: it is what makes the
        budget bind and forgetting the agent's problem.
        """
        # 1. ENTRY
        log.memory.debug(
            "[curated] add: entry",
            extra={"_fields": {
                "target": target, "durability": durability, "chars": len(text),
            }},
        )
        text = self._contain(text)
        if not text:
            return self._failure(target, "Content cannot be empty.")
        if len(text) > self._max_entry_chars(target):
            return self._failure(
                target,
                f"That entry is {len(text)} characters; one entry may use at most "
                f"{self._max_entry_chars(target)} of this target's "
                f"{self.budget_for(target)}-character budget. Split it, or say the "
                f"same thing in fewer words — a single fact that fills the file "
                f"would evict everything else you know.",
            )
        if durability not in DURABILITIES:
            return self._failure(
                target,
                f"durability must be one of {', '.join(DURABILITIES)}. "
                f"There is deliberately no 'transient' — if this will stop being "
                f"true, it does not belong in memory.",
            )

        existing = self.entries(target)
        if any(e.text == text for e in existing):
            return self._success(target, "Entry already present — nothing to do.")

        candidate = [*existing, Entry(text=text, durability=durability)]
        budget = self.budget_for(target)
        projected = len(self._render(candidate))
        evicted: list[Entry] = []
        if projected > budget:
            # 2. DECISION — at capacity. Make room by DECAY first; the
            # consolidation protocol is the fallback when nothing may be dropped.
            candidate, evicted = self._evict_to_fit(candidate, budget)
            projected = len(self._render(candidate))
        if projected > budget:
            return self._at_capacity(target, text, budget)

        self._write(target, candidate)
        # 4. EXIT
        log.memory.info(
            "[curated] add: stored",
            extra={"_fields": {
                "target": target, "durability": durability,
                "used": projected, "budget": budget,
            }},
        )
        note = ""
        if evicted:
            # NEVER a silent delete. The model is told exactly what went, so it can
            # put back anything it still needs — and the log keeps the text, so an
            # eviction is recoverable rather than final.
            for gone in evicted:
                log.memory.warning(
                    "[curated] decay: evicted the oldest until_changed entry to "
                    "make room",
                    extra={"_fields": {
                        "target": target, "freed_chars": len(gone.rendered()),
                        "text": gone.text[:200],
                    }},
                )
            note = (
                " Memory was full, so I made room by dropping the "
                f"{len(evicted)} oldest until_changed "
                f"{'entry' if len(evicted) == 1 else 'entries'}: "
                + "; ".join(f'"{g.text[:60]}"' for g in evicted)
                + ". Re-add anything there that is still true."
            )
        return self._success(
            target,
            # NAME THE DESTINATION. This said only "Saved." regardless of where
            # the fact went, so a misroute was invisible — the failure mode that
            # sank the previous attempt at subject-routing (ESC-48). The target
            # was already in the payload; the sentence a person actually reads
            # never carried it. Naming it converts a silent misroute into one the
            # user corrects in a sentence, and it is what makes INFERRING the
            # target safe enough to do at all.
            f"Saved to {self._describe(target)}. It reaches the system prompt on "
            "the next /new — this conversation keeps the prompt it started "
            "with." + note,
        )

    def replace(self, target: str, old_text: str, new_text: str,
                durability: str) -> MemoryResult:
        """Swap one entry for another. The verb consolidation actually needs."""
        log.memory.debug(
            "[curated] replace: entry",
            extra={"_fields": {"target": target, "chars": len(new_text)}},
        )
        # D08.4 — the SAME containment and per-entry ceiling as `add`. `replace` is
        # the verb the consolidation protocol actively pushes the model toward, so
        # leaving it ungated would have kept the forgery reachable through the door
        # the design recommends. One rule, both writers.
        new_text = self._contain(new_text)
        if not new_text:
            return self._failure(target, "Replacement content cannot be empty.")
        if len(new_text) > self._max_entry_chars(target):
            return self._failure(
                target,
                f"That entry is {len(new_text)} characters; one entry may use at "
                f"most {self._max_entry_chars(target)} of this target's "
                f"{self.budget_for(target)}-character budget.",
            )
        if durability not in DURABILITIES:
            return self._failure(
                target, f"durability must be one of {', '.join(DURABILITIES)}.",
            )
        existing = self.entries(target)
        matched = [e for e in existing if old_text.strip() in e.text]
        if not matched:
            return self._failure(
                target,
                f"No entry matching {old_text[:60]!r}. Use the list below to "
                f"pick an exact one.",
                echo=True,
            )
        replacement = Entry(text=new_text, durability=durability)
        updated = [replacement if e is matched[0] else e for e in existing]
        projected = len(self._render(updated))
        budget = self.budget_for(target)
        if projected > budget:
            return self._at_capacity(target, new_text, budget)
        self._write(target, updated)
        log.memory.info(
            "[curated] replace: stored",
            extra={"_fields": {"target": target, "used": projected}},
        )
        return self._success(target, "Replaced.")

    def remove(self, target: str, text: str) -> MemoryResult:
        """Drop an entry. Destructive, so it matches on substring and reports."""
        log.memory.debug("[curated] remove: entry",
                         extra={"_fields": {"target": target}})
        existing = self.entries(target)
        keep = [e for e in existing if text.strip() not in e.text]
        if len(keep) == len(existing):
            return self._failure(
                target, f"No entry matching {text[:60]!r}.", echo=True,
            )
        self._write(target, keep)
        log.memory.info(
            "[curated] remove: dropped",
            extra={"_fields": {"target": target, "removed": len(existing) - len(keep)}},
        )
        return self._success(target, f"Removed {len(existing) - len(keep)} entry/entries.")

    # -------------------------------------------------------------- internals

    def _render(self, entries: list[Entry]) -> str:
        return ENTRY_DELIMITER.join(e.rendered() for e in entries)

    def _write(self, target: str, entries: list[Entry]) -> None:
        path = self.path_for(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temp file in the same directory then replace, so a crash
        # mid-write cannot leave a truncated profile that the next boot reads as
        # the user's whole identity.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self._render(entries) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _evict_to_fit(
        self, candidate: list[Entry], budget: int,
    ) -> tuple[list[Entry], list[Entry]]:
        """Drop the OLDEST ``until_changed`` entries until the set fits.

        THE FOURTH DEFECT SHAPE, finally actuated. This module's own docstring
        already decided it — "Storing it (not just checking it) lets EVICTION
        prefer ``until_changed`` over ``permanent``" — and nothing ever evicted
        anything. Measured 2026-08-18 over the platform's whole history: 5 removals
        against 36 refusals, and 13,655 characters of fact discarded in three days
        because the file was full.

        The choice at capacity is NOT "lose something or lose nothing" — it is
        "lose the newest fact or the oldest". The old behaviour always discarded
        the newest. For a durability whose name says it holds only *until
        changed*, the newer fact is the better bet.

        ``permanent`` is never touched, which is what separates decay from data
        loss. Order is file order, which IS insertion order because entries are
        appended — so no timestamp had to be invented to know which is oldest.

        Returns (kept, evicted). Evicts the MINIMUM that fits: this stops as soon
        as there is room rather than trimming to some comfortable margin.
        """
        kept = list(candidate)
        evicted: list[Entry] = []
        # The entry just offered is last and is the one being made room FOR, so it
        # is never a candidate for eviction.
        while len(self._render(kept)) > budget:
            # OLDEST FIRST, and deliberately nothing cleverer. A "drop the entry
            # big enough to free the space in one go" rule was tried and reverted
            # 2026-08-19: size is not a staleness signal, and on a real file it
            # selected the OLDEST SUFFICIENT entry, which was a 24-char rule worth
            # keeping — the very thing it was meant to protect. Age is the only
            # staleness evidence this format actually carries, and durability is
            # the control: an entry that should survive pressure belongs in
            # `permanent`, which is never evicted.
            oldest = next(
                (i for i, e in enumerate(kept[:-1])
                 if e.durability == "until_changed"),
                None,
            )
            if oldest is None:
                # Nothing evictable left. Hand back what we have; the caller falls
                # through to the consolidation protocol, which is the honest answer for
                # a file of durable facts.
                break
            evicted.append(kept.pop(oldest))
        return kept, evicted

    def _at_capacity(self, target: str, attempted: str, budget: int) -> MemoryResult:
        """The refusal that makes the budget mean something.

        Bounded: past ``MAX_CONSOLIDATION_FAILURES_PER_TURN`` consecutive
        failures the result goes TERMINAL, dropping the retry instruction, so a
        model that cannot consolidate stops trying and answers the user. A failed
        memory side effect must never suppress the reply.
        """
        self._consolidation_failures += 1
        used = self.used_chars(target)
        entries = self.entries(target)
        if self._consolidation_failures > MAX_CONSOLIDATION_FAILURES_PER_TURN:
            log.memory.warning(
                "[curated] at_capacity: giving up for this turn",
                extra={"_fields": {
                    "target": target, "used": used, "budget": budget,
                    "failures": self._consolidation_failures,
                }},
            )
            return MemoryResult(
                ok=False, done=True, target=target,
                usage=self._usage(used, budget), entry_count=len(entries),
                message=(
                    "Memory is full and consolidation did not succeed this turn. "
                    "Save skipped — continue with the user's request."
                ),
            )
        log.memory.info(
            "[curated] at_capacity: asking for consolidation",
            extra={"_fields": {
                "target": target, "used": used, "budget": budget,
                "attempted_chars": len(attempted),
                "attempt": self._consolidation_failures,
            }},
        )
        return MemoryResult(
            ok=False, done=False, target=target,
            usage=self._usage(used, budget), entry_count=len(entries),
            entries=self._previews(entries),
            message=(
                f"Memory is at {used:,}/{budget:,} chars. Adding this "
                f"({len(attempted)} chars) would exceed the limit. Consolidate "
                f"now: 'replace' to merge overlapping entries into shorter ones, "
                f"or 'remove' what is stale or least important (see "
                f"current_entries), then retry — all in this turn."
            ),
        )

    def _success(self, target: str, message: str) -> MemoryResult:
        # A successful write means consolidation made progress, so the budget
        # resets — the cap counts CONSECUTIVE failures, not lifetime ones.
        self._consolidation_failures = 0
        entries = self.entries(target)
        used = self.used_chars(target)
        return MemoryResult(
            ok=True, done=True, target=target,
            usage=self._usage(used, self.budget_for(target)),
            entry_count=len(entries), message=message,
        )

    def _failure(self, target: str, message: str, *, echo: bool = False) -> MemoryResult:
        entries = self.entries(target)
        return MemoryResult(
            ok=False, done=True, target=target,
            usage=self._usage(self.used_chars(target), self.budget_for(target)),
            entry_count=len(entries), message=message,
            entries=self._previews(entries) if echo else [],
        )

    @staticmethod
    def _usage(used: int, budget: int) -> str:
        pct = min(100, int(used / budget * 100)) if budget else 0
        return f"{pct}% — {used:,}/{budget:,} chars"

    @staticmethod
    def _previews(entries: list[Entry], width: int = 90) -> list[str]:
        return [
            (e.text[:width] + "…") if len(e.text) > width else e.text
            for e in entries
        ]


# --------------------------------------------------------------------------- #
# The nudge (D08.3, pulled into D08.1 by R6Q24).
# --------------------------------------------------------------------------- #

#: Turns without a memory write before the agent is reminded it can make one.
#:
#: WAS 10 — the reference platform's default, adopted "for the same reason as the
#: budgets: it is a tuned number and we have no measurement of our own yet."
#: We have one now, so the borrowed constant no longer applies.
#:
#: MEASURED 2026-08-17 across a week of real traffic. The counter below is
#: in-process, so what matters is not turns per DAY but turns per PROCESS
#: LIFETIME — and this platform restarts often:
#:     2026-08-11    5 boots   10 turns   max  4 per lifetime  ->  2 nudges
#:     2026-08-15   24 boots   14 turns   max  5 per lifetime  ->  0 nudges
#:     2026-08-16   34 boots   46 turns   max 12 per lifetime  ->  1 nudge
#: At 10 the nudge fired ~1/day, and on 08-15 it never fired at all despite the
#: busiest lane taking 14 turns — it simply never reached 10 inside one process.
#: At 4 every one of those days clears the bar.
#:
#: Bakir chose this over persisting the counter (2026-08-17): note_turn() is
#: called from a SYNC prompt-building function, so persistence would make the
#: turn hot path async — a large ripple for a bookkeeping counter. Lowering the
#: threshold removes the symptom without touching that path. The root cause is
#: recorded as debt against D08.3.
NUDGE_INTERVAL_TURNS = 4

#: Turns since the last curated write, per lane. IN-PROCESS, and deliberately
#: not persisted.
#:
#: A restart resets every lane to zero, which means the agent goes another full
#: interval before being nudged again. That is the SAFE direction to be wrong
#: in: under-nudging costs a memory that could have been written, over-nudging
#: costs the user a turn cluttered with the agent talking about its own
#: bookkeeping. Persisting it would buy accuracy in a counter whose only job is
#: to fire "occasionally".
#:
#: THAT REASONING STILL HOLDS, but 2026-08-17 measured how far wrong it went: at
#: 34 boots in a day the reset is not an occasional rounding error, it is the
#: dominant term — the nudge fired ~1/day and on 08-15 not at all. The threshold
#: above absorbs that; this stays in-process deliberately. See D08.3 in
#: progress.yml for the persistence option and why it was not taken.
_TURNS_SINCE_WRITE: dict[str, int] = {}

_NUDGE_TEXT = (
    "You have not recorded anything in memory for a while. If something durable "
    "about the user or about how to do this work has come up — a preference, a "
    "constraint, a correction — record it with the memory tool now. If nothing "
    "has, say nothing and carry on; an empty note is worse than none."
)


def note_turn(session_key: str) -> str | None:
    """Count a turn on ``session_key``; return the nudge text when one is due.

    Returns ``None`` almost always. This exists because with fact extraction
    retired (D08.1 R5Q18) nothing else will ever prompt a write — the agent
    writes when it decides to, and without a periodic reminder "when it decides
    to" measured across a real week is zero.

    The nudge rides the VOLATILE per-turn context, never the system prompt: the
    prompt is frozen per incarnation, so a nudge placed there would either be
    present for the entire conversation or absent from all of it.
    """
    count = _TURNS_SINCE_WRITE.get(session_key, 0) + 1
    if count < NUDGE_INTERVAL_TURNS:
        _TURNS_SINCE_WRITE[session_key] = count
        return None
    _TURNS_SINCE_WRITE[session_key] = 0
    log.memory.info(
        "[curated] nudge: due",
        extra={"_fields": {"session_key": session_key, "turns": count}},
    )
    return _NUDGE_TEXT


def note_write(session_key: str) -> None:
    """Reset the counter — the agent just wrote something, so it needs no hint."""
    _TURNS_SINCE_WRITE[session_key] = 0


def reset_nudges() -> None:
    """Clear every lane's counter. For tests, and for a deliberate restart."""
    _TURNS_SINCE_WRITE.clear()
