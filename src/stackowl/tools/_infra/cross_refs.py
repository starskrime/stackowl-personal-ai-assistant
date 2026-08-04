"""Cross-tool reference stripping (D05.6) — never advertise a tool that is absent.

A schema description that names another tool ("restores what ``edit`` changed",
"use ``transcripts`` for past conversation content") is a routing hint, and a
good one *while that tool is present*. When it is not, the same sentence tells
the model to call something it cannot see — the textbook cause of a hallucinated
tool call.

MEASURED, not assumed: 40 of 77 StackOwl schemas name another tool, 78 references
in all. Most are harmless. After excluding the two cases that CANNOT dangle —

* the target is in the never-evicted base set, so it is always presented;
* referrer and target share a ``requires_capability``, so they vanish together
  (every browser→browser reference is of this kind);

— **31 real exposures across 22 referrers and 14 targets** remain. The clearest
is ``undo_write``: it is always presented, and it advertises ``edit``, which is
not in the base set and is evictable by the budget.

WHY THIS ONLY MATTERS NOW. Before D05.3 every registered tool was presented on
every turn, so a cross-reference was always valid. Capability gating made tools
genuinely disappear, which turned a latent wording issue into a live one.

STRATEGY: SENTENCE-LEVEL REMOVAL, with the cost accepted rather than hidden.
Deleting just the name leaves "restores what changed"; the reference platform
avoids that by exact-string-replacing one pre-written sentence for one tool,
which breaks the moment anyone rewords it. Dropping the whole sentence is generic
and needs no per-tool bookkeeping, but it is BLUNT: ``read_logs``' ANTI-LANE
sentence also carries a still-valid "durable facts (use memory)" hint, and that
half goes with it. Operator decision, taken with that example in front of them.
"""

from __future__ import annotations

import re

from stackowl.infra.observability import log

__all__ = ["strip_dangling_references"]

#: Sentence boundary: a terminator followed by whitespace and a capital/quote.
#: Deliberately conservative — it will NOT split "e.g. foo" or "3.5", which is
#: the right failure direction here: an unsplit sentence means we strip a bit
#: more prose, never that we leave a dangling reference behind.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def strip_dangling_references(
    description: str,
    *,
    tool_name: str,
    presented: frozenset[str],
    catalog: frozenset[str],
    capability_of: dict[str, str | None],
) -> str:
    """Remove sentences naming a tool that is in the catalog but NOT presented.

    ``catalog`` is every registered tool name. A name is only treated as a tool
    reference when it is genuinely registered — otherwise an ordinary English
    word that happens to match ("memory", "process", "wait", "git") would strip
    sentences that were never references at all.

    A reference is KEPT when the target shares this tool's required capability:
    both are gated on the same subsystem, so they are presented together or not
    at all and the hint can never dangle.

    Returns ``description`` unchanged when nothing dangles — the overwhelmingly
    common case, and the one where identity matters because D05.2 memoizes this
    output and an unnecessary rewrite would be a pointless cache difference.
    """
    if not description:
        return description

    own_capability = capability_of.get(tool_name)
    # Only names that are BOTH registered and absent can dangle. Computing the
    # set once avoids scanning the catalog per sentence.
    dangling = {
        name
        for name in catalog
        if name != tool_name
        and name not in presented
        and not (capability_of.get(name) and capability_of.get(name) == own_capability)
    }
    if not dangling:
        return description

    sentences = _SENTENCE_SPLIT.split(description)
    kept: list[str] = []
    dropped: list[str] = []
    for index, sentence in enumerate(sentences):
        hit = next(
            (n for n in dangling if re.search(rf"\b{re.escape(n)}\b", sentence)), None
        )
        # THE FIRST SENTENCE IS NEVER DROPPED. It states what the tool IS; later
        # sentences elaborate or route. Found by a test rather than by design:
        # undo_write opens "Undo the most recent file write/edit by restoring the
        # file's pre-image", and `\bedit\b` matches inside "write/edit", so the
        # blunt rule deleted the tool's own definition and left only "Pass a
        # specific 'token'...". Losing a routing hint is the accepted cost of
        # sentence-level stripping; losing the definition is not.
        #
        # The trade is explicit: a first sentence mentioning an absent tool in
        # PASSING ("write/edit") keeps that word. That is prose, not an
        # instruction to call it — and far less harmful than a tool whose schema
        # no longer says what it does.
        if hit is None or index == 0:
            kept.append(sentence)
            if hit is not None:
                log.tool.debug(
                    "[cross_refs] absent tool named in the FIRST sentence — kept, "
                    "because it defines the tool",
                    extra={"_fields": {"tool": tool_name, "referenced": hit}},
                )
        else:
            dropped.append(hit)

    if not dropped:
        return description

    # Never return an EMPTY description. If every sentence referenced an absent
    # tool, the original is less wrong than nothing at all: a description is what
    # tool_search ranks on and what the model uses to choose, so an empty one
    # would break the tool far more thoroughly than a stale hint.
    if not kept:
        log.tool.warning(
            "[cross_refs] every sentence referenced an absent tool — keeping the "
            "original description rather than emptying it",
            extra={"_fields": {"tool": tool_name, "referenced": sorted(set(dropped))}},
        )
        return description

    log.tool.debug(
        "[cross_refs] stripped dangling reference(s)",
        extra={"_fields": {
            "tool": tool_name, "referenced": sorted(set(dropped)),
            "sentences_dropped": len(dropped),
        }},
    )
    return " ".join(kept)
