"""SecretaryRouter — LLM-driven routing + FuzzyMatcher for owl name correction.

The :class:`FuzzyMatcher` provides a pure-Python edit-distance + difflib match
so that misspelled @OwlName mentions can be auto-suggested (Unicode-safe).

The :class:`SecretaryRouter` asks the fast-tier provider to pick the best
specialist owl for a request. Routing metadata (names + roles) is supplied
verbatim by the user via YAML — only the router glue prompt is in English.
"""

from __future__ import annotations

import difflib
import time
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from stackowl.infra.observability import log
from stackowl.providers.base import Message

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.state import PipelineState
    from stackowl.providers.registry import ProviderRegistry

_DEFAULT_FALLBACK = "secretary"
_FAST_TIER = "fast"
_ROUTING_MAX_TOKENS = 64
_ROUTING_TEMPERATURE = 0.0

_VALID_CLASSES = {"conversational", "standard", "clarify"}


@dataclass(frozen=True)
class RouteResult:
    """Immutable router output: chosen owl + coarse turn classification.

    ``clarify_question`` is the one user-facing question to surface when
    ``intent_class == "clarify"`` (router-authored); None for every other class.
    """

    owl_name: str
    intent_class: Literal["conversational", "standard", "clarify"]
    clarify_question: str | None = None


def _levenshtein(a: str, b: str) -> int:
    """Iterative dynamic-programming edit distance (Unicode-safe).

    Operates on the NFC-normalized code-point sequence of each string so that
    grapheme-equivalent inputs ("café" vs "café") yield the same distance.
    """
    sa = unicodedata.normalize("NFC", a)
    sb = unicodedata.normalize("NFC", b)
    if sa == sb:
        return 0
    if not sa:
        return len(sb)
    if not sb:
        return len(sa)

    previous = list(range(len(sb) + 1))
    current = [0] * (len(sb) + 1)
    for i, ca in enumerate(sa, start=1):
        current[0] = i
        for j, cb in enumerate(sb, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                current[j - 1] + 1,
                previous[j] + 1,
                previous[j - 1] + cost,
            )
        previous, current = current, previous
    return previous[len(sb)]


class FuzzyMatcher:
    """Pure-Python Levenshtein + difflib matcher for owl name correction.

    Combines ``difflib.SequenceMatcher`` ratio (confidence) with an absolute
    edit-distance cap so that long names cannot drift too far from the query.
    """

    def find(
        self,
        query: str,
        candidates: list[str],
        threshold: float = 0.8,
        max_distance: int = 2,
    ) -> tuple[str, float] | None:
        """Return ``(best_match, confidence)`` if a good match exists, else ``None``.

        Args:
            query: User-supplied name (may be misspelled).
            candidates: Known owl names.
            threshold: Minimum SequenceMatcher ratio (0.0-1.0).
            max_distance: Maximum allowed Levenshtein edit distance.
        """
        log.gateway.debug(
            "[fuzzy] find: entry",
            extra={
                "_fields": {
                    "query": query,
                    "candidate_count": len(candidates),
                    "threshold": threshold,
                    "max_distance": max_distance,
                }
            },
        )
        if not query or not candidates:
            log.gateway.debug("[fuzzy] find: empty input — no match")
            return None

        nfc_query = unicodedata.normalize("NFC", query)
        nfc_candidates = [unicodedata.normalize("NFC", c) for c in candidates]

        best: tuple[str, float] | None = None
        for candidate in nfc_candidates:
            ratio = difflib.SequenceMatcher(None, nfc_query, candidate).ratio()
            distance = _levenshtein(nfc_query, candidate)
            log.gateway.debug(
                "[fuzzy] find: candidate scored",
                extra={
                    "_fields": {
                        "candidate": candidate,
                        "ratio": ratio,
                        "distance": distance,
                    }
                },
            )
            if ratio < threshold or distance > max_distance:
                continue
            if best is None or ratio > best[1]:
                best = (candidate, ratio)

        if best is None:
            log.gateway.debug("[fuzzy] find: exit — no candidate met thresholds")
            return None
        log.gateway.debug(
            "[fuzzy] find: exit — match selected",
            extra={"_fields": {"match": best[0], "confidence": best[1]}},
        )
        return best


class SecretaryRouter:
    """Routes requests to the best specialist owl via LLM intent analysis.

    The router calls the fast-tier provider with a compact prompt listing owl
    names and roles. The LLM replies with exactly one name; any malformed or
    unknown response collapses to the default ``secretary`` route.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        owl_registry: OwlRegistry,
    ) -> None:
        self._provider_registry: ProviderRegistry = provider_registry
        self._owl_registry: OwlRegistry = owl_registry
        # PARL-6 (F080) — the in-module fuzzy matcher, now actually wired into
        # _parse_choice so a near-miss owl name (a typo'd @mention) is corrected
        # to a known owl instead of silently collapsing to the secretary.
        self._fuzzy = FuzzyMatcher()

    def _build_prompt(self, owls: list[tuple[str, str]], user_text: str) -> str:
        """Compose the router-glue prompt (English template, user data inlined)."""
        lines = [f"- {name}: {role}" for name, role in owls]
        roster = "\n".join(lines)
        return (
            "You are a router. Reply with ONLY the name of the best owl for "
            'this request, or "secretary" if none fits.\n\n'
            "Available owls:\n"
            f"{roster}\n\n"
            f"User request: {user_text}\n\n"
            "Reply with these lines:\n"
            "Line 1: the owl name (required).\n"
            "Line 2: one of 'conversational', 'standard', or 'clarify':\n"
            "- 'conversational' if you can FULLY answer this yourself, right now, "
            "from your own knowledge — greetings, thanks, opinions, chit-chat, and "
            "any question you can answer or explain directly (a definition, how-to, "
            "advice, a mnemonic, reasoning), with NO need to look anything up, "
            "search, fetch, read a file, run a command, or take any external action. "
            "Do NOT classify as 'conversational' by assuming a generic cloud chatbot "
            "'can't' do something (reach a network, control a device, read local "
            "state) — this assistant may have live local tools this turn that a "
            "plain chatbot would not. Any request to scan, check, run, look up, "
            "verify, or act on the user's own devices/network/system is an external "
            "action — classify it 'standard'.\n"
            "- 'standard' if answering REQUIRES doing, finding, fetching, creating, "
            "or changing something — AND the request is clear enough to act on, OR "
            "the likely action is cheap and reversible (just try it). Directly "
            "addressing or delegating to one of the named owls above (greeting them "
            "by name, handing them a task) is ITSELF an action — routing/delegating "
            "to that specialist — even when the wording alone reads like a plain "
            "greeting or chit-chat; classify it 'standard', never 'conversational'.\n"
            "- 'clarify' is a LAST RESORT, ONLY when the request is genuinely "
            "ambiguous about WHAT to do AND the most likely action is expensive, "
            "slow, irreversible, or you are unsure it is even possible — so a wrong "
            "guess would waste real effort or do harm. BEFORE choosing 'clarify', "
            "first try to resolve the ambiguity yourself: recall what you already "
            "know about the user and the task, and run a cheap, reversible probe or "
            "check; choose 'clarify' only if the ambiguity still remains after that. "
            "When the request is ambiguous but the most likely action is reversible "
            "or cheap, do NOT clarify: choose 'standard', act on the most likely "
            "interpretation, and state your assumption. When torn between 'standard' "
            "and 'clarify', choose 'standard' and act. Judge by meaning, in any "
            "language.\n"
            "Line 3 (ONLY if line 2 is 'clarify'): the single short question to ask "
            "the user, in their language. Omit this line otherwise."
        )

    def _parse_choice(self, raw: str, known_names: set[str]) -> str:
        """Normalize the LLM reply and validate against known owl names."""
        if not raw:
            return _DEFAULT_FALLBACK
        candidate = unicodedata.normalize("NFC", raw).strip().splitlines()[0].strip()
        candidate = candidate.strip("\"'`.,:;()[]{}<>")
        if not candidate:
            return _DEFAULT_FALLBACK
        if candidate in known_names:
            return candidate
        # PARL-6 (F080) — a non-exact candidate is run through the fuzzy matcher
        # BEFORE the secretary fallback. A high-confidence correction ('scoutt' →
        # 'scout') is accepted and logged; only a far miss collapses to secretary.
        # The fallback itself is excluded as a correction target so a typo
        # resolves to a real specialist, never silently to the default.
        candidates = [n for n in known_names if n != _DEFAULT_FALLBACK]
        match = self._fuzzy.find(candidate, candidates)
        if match is not None:
            corrected, confidence = match
            log.engine.info(
                "[router] _parse_choice: fuzzy-corrected near-miss owl name",
                extra={
                    "_fields": {
                        "raw_candidate": candidate,
                        "corrected": corrected,
                        "confidence": confidence,
                    }
                },
            )
            return corrected
        log.engine.debug(
            "[router] _parse_choice: no fuzzy match — secretary fallback",
            extra={"_fields": {"raw_candidate": candidate}},
        )
        return _DEFAULT_FALLBACK

    def _parse_intent_class(
        self, raw: str, trace_id: str | None = None
    ) -> Literal["conversational", "standard", "clarify"]:
        """Scan every line AFTER the owl-name line for the class token.

        A totally EMPTY reply (provider returned nothing — no signal at all)
        fails safe to 'conversational': no tool loop rather than the full
        agentic path. A NON-empty reply that's just missing a valid class
        token keeps the deliberate act-on-likely-intent bias to 'standard'.
        """
        lines = (raw or "").strip().splitlines()
        if not lines:
            log.engine.warning(
                "[router] _parse_intent_class: empty routing reply — "
                "fail-safe to conversational, not standard",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return "conversational"
        for line in lines[1:]:
            token = line.strip().strip("\"'`.,:;()[]{}<>").lower()
            if token in _VALID_CLASSES:
                return token  # type: ignore[return-value]
        log.engine.warning(
            "[router] _parse_intent_class: no valid class token — fail-safe to standard",
            extra={"_fields": {"trace_id": trace_id, "raw_preview": raw[:80]}},
        )
        return "standard"

    def _parse_clarify_question(self, raw: str, intent_class: str) -> str | None:
        """Extract the line-3 clarifying question for a 'clarify' verdict.

        The question is every non-empty line AFTER the line that carried the
        class token, joined with spaces. Returns None for any non-clarify class
        OR when no question text follows (caller downgrades clarify→standard).

        Lines whose entire normalized content equals a class token are dropped
        so that a degenerate reply like "secretary\\nclarify\\nstandard" does NOT
        ship "standard" as the question — it yields None and the caller
        downgrades the verdict to standard.  Only a line that IS a class token
        (after normalization) is dropped; a line that merely CONTAINS a class
        word inside natural language is kept unchanged.
        """
        if intent_class != "clarify":
            return None
        lines = (raw or "").strip().splitlines()
        # find the index of the line bearing the clarify token, then take the rest
        for i, line in enumerate(lines[1:], start=1):
            token = line.strip().strip("\"'`.,:;()[]{}<>").lower()
            if token in _VALID_CLASSES:
                rest = [
                    ln.strip()
                    for ln in lines[i + 1:]
                    if ln.strip()
                    # drop any line that, when normalized, IS itself a class token
                    and ln.strip().strip("\"'`.,:;()[]{}<>").lower() not in _VALID_CLASSES
                ]
                question = " ".join(rest).strip()
                return question or None
        return None

    async def route(self, state: PipelineState) -> RouteResult:
        """Call the fast-tier provider and return RouteResult(owl_name, intent_class).

        Falls back to ``secretary`` / ``standard`` on any provider failure, empty
        reply, or unknown owl. Owl selection is line-1-only (byte-identical to
        prior behavior). Intent class is an OPTIONAL line-2 token; fail-safe to
        ``standard``. The routing LLM call's cost is recorded by the PROVIDER
        itself (E8-S0cost single recording site), so the router records nothing.
        """
        log.engine.debug(
            "[router] route: entry",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "session_id": state.session_id,
                    "input_len": len(state.input_text),
                }
            },
        )

        owls = self._owl_registry.list()
        owl_pairs = [(m.name, m.role) for m in owls]
        known_names = {name for name, _ in owl_pairs}
        if _DEFAULT_FALLBACK not in known_names:
            known_names.add(_DEFAULT_FALLBACK)

        log.engine.debug(
            "[router] route: roster built",
            extra={"_fields": {"owl_count": len(owl_pairs)}},
        )

        prompt = self._build_prompt(owl_pairs, state.input_text)
        messages = [Message(role="user", content=prompt)]

        try:
            provider, model = self._provider_registry.get_with_cascade_and_model(_FAST_TIER)
        except Exception as exc:  # noqa: BLE001 — defensive: never block routing
            log.engine.error(
                "[router] route: provider cascade failed — falling back to secretary",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return RouteResult(_DEFAULT_FALLBACK, "standard")

        log.engine.debug(
            "[router] route: provider selected",
            extra={"_fields": {"provider": provider.name}},
        )

        t0 = time.monotonic()
        try:
            result = await provider.complete(
                messages,
                model=model,
                max_tokens=_ROUTING_MAX_TOKENS,
                temperature=_ROUTING_TEMPERATURE,
                # A reasoning-capable provider (e.g. a vLLM/Qwen3-style endpoint)
                # burns its entire max_tokens budget on <think> chain-of-thought
                # before ever emitting the owl-name/intent-class verdict this
                # prompt asks for, so `result.content` comes back empty every
                # time (root cause of "[router] _parse_intent_class: empty
                # routing reply") — the same failure mode the other five
                # interaction/*_classifier.py callers already avoid via this
                # exact flag (built 2026-07-11, "Wired disable_thinking through
                # complete() to providers"); the router call was the one
                # structured/classifier call site that never got wired to it.
                disable_thinking=True,
            )
        except Exception as exc:  # noqa: BLE001 — defensive: never block routing
            duration_ms = (time.monotonic() - t0) * 1000
            log.engine.error(
                "[router] route: provider.complete failed — falling back to secretary",
                exc_info=exc,
                extra={
                    "_fields": {
                        "trace_id": state.trace_id,
                        "provider": provider.name,
                        "latency_ms": duration_ms,
                    }
                },
            )
            return RouteResult(_DEFAULT_FALLBACK, "standard")

        duration_ms = (time.monotonic() - t0) * 1000
        log.engine.debug(
            "[router] route: provider replied",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "provider": provider.name,
                    "latency_ms": duration_ms,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "reply_len": len(result.content),
                }
            },
        )

        owl = self._parse_choice(result.content, known_names)  # UNCHANGED owl parse
        intent_class = self._parse_intent_class(result.content, state.trace_id)
        clarify_question = self._parse_clarify_question(result.content, intent_class)
        if intent_class == "clarify" and not clarify_question:
            # A clarify verdict with no question is malformed — downgrade to the
            # conservative default (act) so we never surface an empty question.
            log.engine.info(
                "[router] route: clarify verdict had no question — downgrading to standard",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            intent_class = "standard"

        # E8-S0cost — the routing call's cost is recorded by the PROVIDER inside
        # provider.complete (single recording site), so the router records nothing
        # here — recording it again would DOUBLE-COUNT the routing spend.

        log.engine.debug(
            "[router] route: exit",
            extra={"_fields": {"owl": owl, "intent_class": intent_class}},
        )
        log.engine.info(
            "[router] selected %s intent_class=%s (LLM decision, %.1fms)",
            owl,
            intent_class,
            duration_ms,
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "owl": owl,
                    "intent_class": intent_class,
                    "latency_ms": duration_ms,
                    "provider": provider.name,
                }
            },
        )

        return RouteResult(owl, intent_class, clarify_question)
