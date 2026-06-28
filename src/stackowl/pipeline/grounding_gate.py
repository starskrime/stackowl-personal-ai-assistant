"""surface_grounding_gate — anti-fabrication citation integrity (ADR-T3 / TS5+TS6).

The bug this kills: the model presented "AI news" with FAKE links (a made-up
GPT-5.6 announcement, a fabricated URL) while NO web_search/web_fetch ever ran —
pure hallucination dressed as sourced fact.

MEASURED, never prose-scanned. The rule keys ONLY on URLs + the per-turn retrieval
ledger (``state.tool_calls``), never on classifying the topic of the answer:

  * FETCHED-SOURCE SET — every URL that actually came back from a ``web_search``
    result or was successfully ``web_fetch``'d THIS turn (read from the tool
    records). This is the only set of URLs the turn is entitled to cite.
  * Any http(s) URL in the answer NOT in the fetched set (and not one the USER
    themselves pasted this turn — echoing the user is not fabrication) is a
    FABRICATED citation → stripped.
  * TS5 retrieval gate: if the answer carries external URLs but the turn retrieved
    ZERO sources — no retrieval tool ran, OR one ran and came back EMPTY (the
    "empty scheduled cycle": a recurring poke whose web_search found nothing) —
    every URL is fabricated by definition and the external claim is ungrounded →
    floor to the honest "I didn't actually look this up", never a husk of invented
    prose with the link stripped. Likewise if stripping guts the answer → floor.

Runs pre-deliver in both backends, AFTER the give-up / overclaim floors (so it
no-ops on an already-floored draft). Never raises. Byte-identical when the answer
contains no URLs.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

# Tools whose results contribute to the fetched-source set. Both declare
# capability_tag="web_knowledge"; keyed by name because the parse shape differs per
# tool. ponytail: extend this set if a new first-class retrieval tool lands.
_RETRIEVAL_TOOLS = frozenset({"web_search", "web_fetch"})

# Unicode-safe http(s) URL scanner. Matches the scheme + everything up to the first
# whitespace or a delimiter that cannot be part of a URL; trailing punctuation is
# trimmed separately so "(see https://x.com/a.)" yields "https://x.com/a".
_URL_RE = re.compile(r"https?://[^\s<>\)\]\"'`]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?。．)]}>\"'`*"

# A stripped answer with fewer than this many Unicode word-chars left is "gutted"
# (the URLs were carrying the substance) → floor instead of delivering a husk.
_MIN_SUBSTANCE_WORDCHARS = 20
_WORDCHAR_RE = re.compile(r"\w", re.UNICODE)

_FLOOR_TEXT = (
    "I couldn't verify sources for this — I didn't actually retrieve it, so I "
    "can't stand behind those links. Want me to look it up properly?"
)


def _normalize_url(raw: str) -> str:
    """Canonicalize a URL for set membership: lowercase scheme+host, drop the
    fragment, strip a trailing path slash. Query is KEPT (a different query is a
    different page). Returns "" for an unparseable value."""
    try:
        parts = urlsplit(raw.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            return ""
        path = parts.path.rstrip("/")
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
        )
    except ValueError:
        return ""


def _extract_urls(text: str) -> list[str]:
    """Every http(s) URL in ``text`` (raw, trailing punctuation trimmed), in order."""
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(_TRAILING_PUNCT)
        if url:
            out.append(url)
    return out


def _fetched_source_set(state: PipelineState) -> set[str]:
    """Normalized URLs the turn actually retrieved: every ``url`` field returned by a
    ``web_search`` result + every successfully ``web_fetch``'d URL. Read from the
    immutable per-turn tool records — the MEASURED ledger, not the answer prose."""
    fetched: set[str] = set()
    for call in state.tool_calls:
        if call.tool_name not in _RETRIEVAL_TOOLS:
            continue
        if call.tool_name == "web_search":
            # Result is the JSON envelope: {"data": {"web": [{"url": ...}, ...]}}.
            # Pull every URL textually — robust to shape drift, never raises.
            for url in _extract_urls(call.result or ""):
                norm = _normalize_url(url)
                if norm:
                    fetched.add(norm)
        else:  # web_fetch — the fetched URL is its own source, when the fetch succeeded.
            if call.error:
                continue
            norm = _normalize_url(str(call.args.get("url", "")))
            if norm:
                fetched.add(norm)
    return fetched


def _retrieval_ran(state: PipelineState) -> bool:
    """True iff any retrieval tool was invoked this turn (success or empty)."""
    return any(c.tool_name in _RETRIEVAL_TOOLS for c in state.tool_calls)


def _answer_text(state: PipelineState) -> str:
    """Concatenated text of the durable ANSWER chunks (progress chunks excluded)."""
    return "".join(c.content for c in state.responses if c.kind == "answer")


def _strip_urls(text: str, fabricated: set[str]) -> str:
    """Remove fabricated URLs from ``text``. A markdown link ``[label](badurl)``
    collapses to ``label``; a bare URL is removed. Membership is by normalized form."""

    def _md(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        return label if _normalize_url(url) in fabricated else match.group(0)

    text = re.sub(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", _md, text)

    def _bare(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        return "" if _normalize_url(url) in fabricated else match.group(0)

    return _URL_RE.sub(_bare, text)


def _is_gutted(text: str) -> bool:
    """True if too little substance remains to stand as an answer."""
    return len(_WORDCHAR_RE.findall(text)) < _MIN_SUBSTANCE_WORDCHARS


def _grounding_floor_chunk(state: PipelineState) -> ResponseChunk:
    """Deterministic honest floor for an ungrounded external-info answer."""
    return ResponseChunk(
        content=_FLOOR_TEXT,
        is_final=False,
        chunk_index=0,
        trace_id=state.trace_id,
        owl_name=state.owl_name,
        is_floor=True,
    )


async def surface_grounding_gate(state: PipelineState) -> PipelineState:
    """Strip fabricated citations / floor an ungrounded external-info answer.

    1. ENTRY — gather the answer URLs.
    2. DECISION — which URLs are fabricated (not fetched, not user-supplied).
    3. STEP — strip them, or floor if retrieval never ran / the answer is gutted.
    4. EXIT — evolved state, or the original (byte-identical) when nothing to do.
    Never raises: on any internal error the original draft is returned untouched.
    """
    try:
        # Skip an already-floored or empty draft (give-up / overclaim already spoke).
        if not state.responses or any(
            getattr(c, "is_floor", False) for c in state.responses
        ):
            return state
        answer = _answer_text(state)
        response_urls = _extract_urls(answer)
        if not response_urls:
            return state  # byte-identical: no URLs, nothing to ground

        # 2. DECISION — user-supplied URLs are exempt (echoing the user ≠ fabrication).
        user_urls = {_normalize_url(u) for u in _extract_urls(state.input_text)}
        user_urls.discard("")
        fetched = _fetched_source_set(state)
        fabricated = {
            norm
            for u in response_urls
            if (norm := _normalize_url(u)) and norm not in fetched and norm not in user_urls
        }
        if not fabricated:
            return state  # every URL is grounded or user-supplied — back-compat

        retrieval_ran = _retrieval_ran(state)
        log.engine.warning(
            "grounding.fabricated_citations",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "n_fabricated": len(fabricated),
                    "n_fetched": len(fetched),
                    "retrieval_ran": retrieval_ran,
                }
            },
        )

        # 3. STEP — TS5: floor when the turn RETRIEVED ZERO SOURCES. This covers BOTH
        # "no retrieval tool ran" AND the dangerous "empty scheduled cycle" — a
        # web_search that ran but came back EMPTY (ADR-T5, Mary's #1 risk: a 2-hourly
        # poke whose search found nothing must never fabricate). With no fetched
        # source, every cited URL is fabricated by definition and the external claim
        # is wholly ungrounded; stripping the URLs would leave an ungrounded HUSK of
        # invented prose (e.g. "GPT-5.6 just launched" with the link removed). Floor
        # instead. When SOME source WAS fetched, fall through and strip only the
        # fabricated URLs, keeping the grounded remainder.
        if not fetched:
            log.engine.warning(
                "grounding.floored_no_sources",
                extra={"_fields": {
                    "trace_id": state.trace_id, "retrieval_ran": retrieval_ran,
                }},
            )
            return state.evolve(
                responses=(_grounding_floor_chunk(state),), overclaim_blocked=True
            )

        stripped = tuple(
            c.model_copy(update={"content": _strip_urls(c.content, fabricated)})
            if c.kind == "answer"
            else c
            for c in state.responses
        )
        if _is_gutted(_answer_text(state.evolve(responses=stripped))):
            log.engine.warning(
                "grounding.floored_gutted",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state.evolve(
                responses=(_grounding_floor_chunk(state),), overclaim_blocked=True
            )

        # 4. EXIT — deliver the answer with fabricated citations stripped.
        log.engine.info(
            "grounding.stripped",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "n_stripped": len(fabricated),
                }
            },
        )
        return state.evolve(responses=stripped)
    except Exception as exc:
        log.engine.error(
            "[grounding_gate] internal error — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
