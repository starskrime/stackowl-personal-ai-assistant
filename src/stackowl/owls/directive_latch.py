"""In-memory Schmitt-trigger hysteresis for DNA->prompt directives. Per (owl, trait, direction)
latch: a HIGH directive turns on at >=0.62 and stays on until the trait drops <0.55 (symmetric
LOW at <=0.38 / >0.45). FR-1 narrowed these bands (were 0.70/0.60 and 0.30/0.40) so directives
actually fire under real evolution deltas; ENTER stays stricter than EXIT on both sides so the
hysteresis still prevents flapping. In-memory (DNA changes per-batch, not per-turn; cold-start
re-seeds from value); fail-OPEN (any error -> plain threshold). Module-singleton, mirrors
FOCUS_TRACKER."""
from __future__ import annotations

from threading import Lock

from stackowl.infra.observability import log

HIGH_ENTER = 0.62
HIGH_EXIT = 0.55
LOW_ENTER = 0.38
LOW_EXIT = 0.45
_MAX_KEYS = 512


class DirectiveLatch:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], list[bool]] = {}  # (owl, trait) -> [high_on, low_on]
        self._lock = Lock()

    def _entry(self, owl: str, trait: str) -> list[bool] | None:
        """Return the existing entry, or insert a fresh [False, False] and return None to signal
        'not yet seeded' on first touch for this (owl, trait)."""
        key = (owl, trait)
        e = self._by_key.get(key)
        if e is None:
            if len(self._by_key) >= _MAX_KEYS:
                self._by_key.pop(next(iter(self._by_key)))
            self._by_key[key] = [False, False]
            return None
        return e

    def high_state(self, owl: str, trait: str, value: float) -> bool:
        try:
            with self._lock:
                e = self._entry(owl, trait)
                if e is None:  # lazy seed = plain threshold
                    seeded = value >= HIGH_ENTER
                    self._by_key[(owl, trait)][0] = seeded
                    return seeded
                if value >= HIGH_ENTER:
                    new = True
                elif value < HIGH_EXIT:
                    new = False
                else:
                    new = e[0]  # hold in deadband
                if new != e[0]:
                    log.engine.info(
                        "[owls] directive_latch.flip",
                        extra={"_fields": {
                            "owl": owl, "trait": trait, "dir": "high",
                            "old": e[0], "new": new, "value": value,
                        }},
                    )
                e[0] = new
                return new
        except Exception as exc:
            log.engine.error(
                "[owls] directive_latch.high_state failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl, "trait": trait}},
            )
            return value >= HIGH_ENTER

    def low_state(self, owl: str, trait: str, value: float) -> bool:
        try:
            with self._lock:
                e = self._entry(owl, trait)
                if e is None:  # lazy seed = plain threshold
                    seeded = value <= LOW_ENTER
                    self._by_key[(owl, trait)][1] = seeded
                    return seeded
                if value <= LOW_ENTER:
                    new = True
                elif value > LOW_EXIT:
                    new = False
                else:
                    new = e[1]  # hold in deadband
                if new != e[1]:
                    log.engine.info(
                        "[owls] directive_latch.flip",
                        extra={"_fields": {
                            "owl": owl, "trait": trait, "dir": "low",
                            "old": e[1], "new": new, "value": value,
                        }},
                    )
                e[1] = new
                return new
        except Exception as exc:
            log.engine.error(
                "[owls] directive_latch.low_state failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl, "trait": trait}},
            )
            return value <= LOW_ENTER

    def reset_owl(self, owl: str) -> None:
        try:
            with self._lock:
                for key in [k for k in self._by_key if k[0] == owl]:
                    del self._by_key[key]
        except Exception as exc:
            log.engine.error(
                "[owls] directive_latch.reset_owl failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl}},
            )

    def clear_all(self) -> None:
        with self._lock:
            self._by_key.clear()


DIRECTIVE_LATCH = DirectiveLatch()
