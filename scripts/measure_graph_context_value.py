"""Offline replay: is the Kuzu graph context worth anything?

Bakir, 2026-08-31: measure before rebuilding. If graph context contributes
nothing, the agreed purge-and-rebuild does not happen at all.

WHAT THIS DOES. For every real user message (machine lanes excluded), it
reproduces the context `classify` WOULD have injected and asks whether any of it
appears in the answer that turn actually produced.

IT IMPORTS THE PRODUCTION FUNCTIONS RATHER THAN REIMPLEMENTING THEM.
`_candidate_entity_ids` derives the entity ids and `sync_traverse_many` runs the
traversal — the same two the live pipeline calls. A hand-written copy would
measure my reimplementation, which is the "fixture that stopped resembling the
real thing" failure this project keeps paying for.

READ-ONLY, ON A COPY. The live graph is held by the running core; this copies it
and opens the copy read-only, so a measurement can never damage what it measures.

THE SIGNAL IS LEXICAL OVERLAP, deliberately. Bakir chose it as primary with a
judge only on a sample: this programme has measured LLM judges near chance when
assessing a trajectory they can see. Overlap UNDERCOUNTS — context can steer an
answer without appearing in it — so it is an honest floor, not a fair estimate,
and it is reported as such.

Usage:  uv run python scripts/measure_graph_context_value.py [--limit N]
"""

from __future__ import annotations

import argparse
import collections
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stackowl.paths import StackowlHome  # noqa: E402
from stackowl.pipeline.steps.classify import _candidate_entity_ids  # noqa: E402
from stackowl.sessions.models import MACHINE_LANE_PREFIXES  # noqa: E402

#: Mirrors classify's own cap on how many entities reach the prompt.
_INJECTED_MAX = 10
#: Below this length a token match is noise ("AI" appearing in an answer says
#: nothing). Reported separately rather than silently applied to everything.
_MIN_MEANINGFUL = 4

_PAIRS_SQL = """
SELECT m.conversation_id AS cid, m.role AS role, m.content AS content,
       m.created_at AS ts, v.session_key AS session_key
  FROM messages m
  JOIN conversations v ON v.id = m.conversation_id
 ORDER BY m.conversation_id, m.created_at, m.id
"""


def _turn_pairs(db: Path, limit: int | None) -> list[tuple[str, str]]:
    """(user message, the assistant reply that followed it), real lanes only."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    pairs: list[tuple[str, str]] = []
    pending: str | None = None
    for row in conn.execute(_PAIRS_SQL):
        key = str(row["session_key"] or "")
        if key.startswith(MACHINE_LANE_PREFIXES):
            pending = None
            continue
        if row["role"] == "user":
            pending = str(row["content"] or "")
        elif row["role"] == "assistant" and pending is not None:
            pairs.append((pending, str(row["content"] or "")))
            pending = None
            if limit and len(pairs) >= limit:
                break
    conn.close()
    return pairs


def _graph_copy() -> Path:
    src = StackowlHome.home() / "kuzu" / "graph.kuzu"
    tmp = Path(tempfile.mkdtemp(prefix="graph-replay-"))
    shutil.copy2(src, tmp / "graph.kuzu")
    wal = src.with_suffix(".kuzu.wal")
    if wal.exists():
        shutil.copy2(wal, tmp / "graph.kuzu.wal")
    return tmp / "graph.kuzu"


def _injected(conn: object, question: str) -> list[tuple[str, str]]:
    """The (name, entity_type) list classify would have put in the prompt."""
    ids = _candidate_entity_ids(question)
    if not ids:
        return []
    cypher = (
        "MATCH (f:Fact)-[:MENTIONS]->(s2:Entity), (f)-[:MENTIONS]->(o2:Entity) "
        "WHERE list_contains($ids, s2.id) AND o2.id <> s2.id "
        "RETURN DISTINCT o2.name AS name, o2.entity_type AS t"
    )
    result = conn.execute(cypher, {"ids": ids})  # type: ignore[attr-defined]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    while result.has_next():
        name, etype = result.get_next()
        if name and name not in seen:
            seen.add(name)
            out.append((str(name), str(etype or "")))
        if len(out) >= _INJECTED_MAX:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    import kuzu

    db_path = StackowlHome.db_path()
    pairs = _turn_pairs(db_path, args.limit)
    graph = _graph_copy()
    conn = kuzu.Connection(kuzu.Database(str(graph), read_only=True))

    turns = 0
    with_context = 0
    used_any = 0
    used_meaningful = 0
    used_novel = 0
    entity_hits: collections.Counter[str] = collections.Counter()
    injected_total = 0
    empty_context = 0

    for question, answer in pairs:
        turns += 1
        injected = _injected(conn, question)
        if not injected:
            empty_context += 1
            continue
        with_context += 1
        injected_total += len(injected)
        low = answer.casefold()
        asked = question.casefold()
        hit = [n for n, _t in injected if n.casefold() in low]
        meaningful = [n for n in hit if len(n) >= _MIN_MEANINGFUL]
        # THE CONTROL THAT DECIDES THE ANSWER. An entity that was ALREADY IN THE
        # QUESTION would appear in the reply whether the graph existed or not, so
        # counting it credits the graph for the user's own words. Only an entity
        # the graph SUPPLIED — present in the answer, absent from the question —
        # is evidence the graph contributed anything.
        novel = [n for n in meaningful if n.casefold() not in asked]
        if hit:
            used_any += 1
        if meaningful:
            used_meaningful += 1
        if novel:
            used_novel += 1
            entity_hits.update(novel)

    print(f"turns replayed                 {turns}")
    print(f"  got NO graph context         {empty_context}")
    print(f"  got graph context            {with_context}")
    if with_context:
        print(f"  mean entities injected       {injected_total / with_context:.1f}")
        print(
            f"  answer contained ANY of them {used_any} "
            f"({100 * used_any / with_context:.1f}% of contexted turns)"
        )
        print(
            f"  ...excluding tokens < {_MIN_MEANINGFUL} chars {used_meaningful} "
            f"({100 * used_meaningful / with_context:.1f}%)"
        )
        print(
            f"  ...AND absent from the question {used_novel} "
            f"({100 * used_novel / with_context:.1f}%)   <-- the only number that counts"
        )
    # AND WHAT ARE THOSE HITS MADE OF? A rate is not a finding until you know
    # what is inside it. The graph is 28.2% platform diagnostics by construction,
    # so a hit on "traces" or "failure class" is far more likely to be the answer
    # discussing debugging than the graph having contributed anything.
    internal = re.compile(
        r"\b(trace|failure|retry|tool|skill|shell|session|owl|cron|schedul|"
        r"substitut|unachieved|effect|node|memory|system|json|budget|guard|"
        r"pipeline|log|error|attempt|task|web_fetch|tool_search)", re.I,
    )
    plat = sum(n for name, n in entity_hits.items() if internal.search(name))
    total = sum(entity_hits.values())
    print("\ntop entities the GRAPH supplied that reached an answer:")
    for name, n in entity_hits.most_common(15):
        mark = "  [platform]" if internal.search(name) else ""
        print(f"  {n:5}  {name}{mark}")
    if total:
        print(
            f"\nhits that are PLATFORM-INTERNAL vocabulary: {plat}/{total} "
            f"({100 * plat / total:.0f}%) — these are words an answer about "
            f"debugging would contain anyway"
        )
    shutil.rmtree(graph.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
