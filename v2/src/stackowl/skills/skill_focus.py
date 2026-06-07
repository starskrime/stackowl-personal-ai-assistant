"""Cross-turn skill focus (hysteresis) for relevance-tiering. In-memory, (owl, session)-scoped.

A focus HEURISTIC, not durable data: cold-start on restart costs at most one turn. Makes a skill
that was ACTIVE last turn — or recently skill_view'd — stickier, so it's easier to STAY active than
to ENTER. Module-singleton (mirrors the _skill_injector pattern); thread-safe (sync Lock)."""
from __future__ import annotations

from threading import Lock

from stackowl.infra.observability import log

ACTIVE_BONUS = 0.15
VIEW_BONUS = 0.25
DECAY = 0.5
FOCUS_DECAY_TURNS = 3
_MAX_KEYS = 512


def _decayed(base: float, last_turn: int, current_turn: int) -> float:
    """Full `base` the turn immediately after the event, decaying to 0 over FOCUS_DECAY_TURNS."""
    diff = current_turn - last_turn
    if diff < 1 or diff > FOCUS_DECAY_TURNS:
        return 0.0
    return base * (DECAY ** (diff - 1))


class _Focus:
    __slots__ = ("turn", "active", "viewed")

    def __init__(self) -> None:
        self.turn = 0
        self.active: dict[str, int] = {}
        self.viewed: dict[str, int] = {}


class SkillFocusTracker:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], _Focus] = {}
        self._lock = Lock()

    def _get(self, owl: str, session: str) -> _Focus:
        key = (owl, session)
        f = self._by_key.get(key)
        if f is None:
            if len(self._by_key) >= _MAX_KEYS:
                self._by_key.pop(next(iter(self._by_key)))
            f = _Focus()
            self._by_key[key] = f
        return f

    def begin_turn(self, owl: str, session: str) -> int:
        try:
            with self._lock:
                f = self._get(owl, session)
                f.turn += 1
                return f.turn
        except Exception as exc:
            log.engine.error(
                "[skill_focus] begin_turn failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl}},
            )
            return 0

    def bonus_for(self, owl: str, session: str, name: str, current_turn: int) -> float:
        try:
            with self._lock:
                f = self._by_key.get((owl, session))
                if f is None:
                    return 0.0
                a = _decayed(ACTIVE_BONUS, f.active.get(name, -10), current_turn)
                v = _decayed(VIEW_BONUS, f.viewed.get(name, -10), current_turn)
                return max(a, v)
        except Exception as exc:
            log.engine.error(
                "[skill_focus] bonus_for failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl}},
            )
            return 0.0

    def mark_active(self, owl: str, session: str, names: list[str], turn: int) -> None:
        try:
            with self._lock:
                f = self._get(owl, session)
                for n in names:
                    f.active[n] = turn
        except Exception as exc:
            log.engine.error(
                "[skill_focus] mark_active failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl}},
            )

    def mark_viewed(self, owl: str, session: str, name: str, turn: int) -> None:
        try:
            with self._lock:
                f = self._get(owl, session)
                f.viewed[name] = turn
        except Exception as exc:
            log.engine.error(
                "[skill_focus] mark_viewed failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl}},
            )

    def clear_all(self) -> None:
        with self._lock:
            self._by_key.clear()


FOCUS_TRACKER = SkillFocusTracker()
