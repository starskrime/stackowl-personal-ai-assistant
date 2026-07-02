"""Per-field, self-correcting pydantic ``ValidationError`` formatting.

A weak model that gets back pydantic's raw dump (jargon + an ``errors.pydantic.dev``
URL) or a generic message naming the wrong field burns retries without ever
converging — worse, a deliberately strict circuit breaker (:class:`TurnProgressTracker`,
out of scope here) permanently bounces it before it can. :func:`format_validation_error`
names exactly the field(s) actually wrong so the next call can be a real fix.
"""

from __future__ import annotations

from pydantic import ValidationError


def format_validation_error(exc: ValidationError, tool_name: str) -> str:
    """Turn a pydantic ``ValidationError`` into an actionable per-field message.

    Never the raw pydantic dump, never a message that names the wrong field.
    """
    parts = [
        f"'{'.'.join(str(p) for p in e['loc']) or '(top level)'}': {e['msg']}"
        for e in exc.errors()
    ]
    return (
        f"invalid arguments for '{tool_name}' — " + "; ".join(parts) +
        ". Re-issue the call fixing only the field(s) named above."
    )


if __name__ == "__main__":
    from pydantic import BaseModel, ConfigDict

    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")
        action: str
        schedule: str | None = None

    try:
        _Args(action=1, schedule=2)  # type: ignore[arg-type]
    except ValidationError as _exc:
        msg = format_validation_error(_exc, "demo")
        assert "'action'" in msg and "'schedule'" in msg, msg
        assert "errors.pydantic.dev" not in msg, msg
        print("ok:", msg)
