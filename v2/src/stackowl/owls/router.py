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
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.providers.base import Message

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.state import PipelineState
    from stackowl.providers.cost_tracker import CostTracker
    from stackowl.providers.registry import ProviderRegistry

_DEFAULT_FALLBACK = "secretary"
_FAST_TIER = "fast"
_ROUTING_MAX_TOKENS = 32
_ROUTING_TEMPERATURE = 0.0


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
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider_registry: ProviderRegistry = provider_registry
        self._owl_registry: OwlRegistry = owl_registry
        self._cost_tracker: CostTracker | None = cost_tracker

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
            "Reply with exactly one owl name:"
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
        return _DEFAULT_FALLBACK

    async def route(self, state: PipelineState) -> str:
        """Call the fast-tier provider and return the chosen owl name.

        Falls back to ``secretary`` on any provider failure, empty reply, or
        unknown owl. Records cost via ``CostTracker`` when one is configured.
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
            provider = self._provider_registry.get_with_cascade(_FAST_TIER)
        except Exception as exc:  # noqa: BLE001 — defensive: never block routing
            log.engine.error(
                "[router] route: provider cascade failed — falling back to secretary",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return _DEFAULT_FALLBACK

        log.engine.debug(
            "[router] route: provider selected",
            extra={"_fields": {"provider": provider.name}},
        )

        t0 = time.monotonic()
        try:
            result = await provider.complete(
                messages,
                model="",
                max_tokens=_ROUTING_MAX_TOKENS,
                temperature=_ROUTING_TEMPERATURE,
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
            return _DEFAULT_FALLBACK

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

        chosen = self._parse_choice(result.content, known_names)

        if self._cost_tracker is not None:
            try:
                await self._cost_tracker.record(
                    provider_name=provider.name,
                    model="routing",
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    duration_ms=duration_ms,
                    trace_id=state.trace_id,
                )
            except Exception as exc:  # noqa: BLE001 — cost tracking is best-effort
                log.engine.warning(
                    "[router] route: cost_tracker.record failed",
                    exc_info=exc,
                    extra={
                        "_fields": {
                            "trace_id": state.trace_id,
                            "provider": provider.name,
                        }
                    },
                )

        log.engine.info(
            "[router] selected %s (LLM decision, %.1fms)",
            chosen,
            duration_ms,
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "owl": chosen,
                    "latency_ms": duration_ms,
                    "provider": provider.name,
                }
            },
        )

        return chosen
