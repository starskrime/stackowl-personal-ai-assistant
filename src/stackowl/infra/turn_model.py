"""Which model is running THIS turn — a turn-scoped carrier (ESC-47/50).

WHY A ContextVar AND NOT AN ARGUMENT. The model is chosen in ``execute`` when a
provider plan is resolved, and needed in ``TaskOutcomeStore.record`` several hops
later, after the turn has finished. On the streaming path there is no
``CompletionResult`` to carry it back — a stream yields text, not a result
object. This is the same shape ``lesson_experiment`` already solves for the
lessons arm, and ``record()`` already reads that one the same way ("classify
decides the arm, and this recorder is several hops away"). One more carrier of an
established idiom, not a second engine.

A BLANK STAMP NEVER OVERWRITES A REAL ONE. ``assemble`` resolves a provider plan
purely to size the context window, and when no provider is configured that
resolves to an empty model. Letting that clear a real selection would make a
cached-prompt turn record the probe instead of what ran.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

#: Empty means UNKNOWN, and unknown is written as NULL. Never a placeholder
#: string: "unknown" is a value later analysis would group on as though it named
#: something.
_model: ContextVar[str] = ContextVar("turn_model", default="")


def set_model(name: str) -> Token[str]:
    """Stamp the model for this turn. A blank name is ignored, not stored."""
    if not name:
        return _model.set(_model.get())
    return _model.set(name)


def current_model() -> str:
    """The model running this turn, or "" when nothing has been stamped."""
    return _model.get()


def reset(token: Token[str]) -> None:
    """Restore the previous value — used by tests and by turn teardown."""
    _model.reset(token)
