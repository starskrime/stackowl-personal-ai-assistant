"""LearnedToolSpec — the declarative spec for an agent-authored (learned) tool.

The SAFETY CORE of H4 ``tool_build``. A learned tool is NOT model-authored Python:
it is a frozen declaration of a command to run through the existing allowlisted
shell ``create_subprocess_exec`` boundary. The model supplies:

* a fixed ``argv_template`` — a LIST (argv), never a shell string. ``argv[0]`` is a
  fixed literal program name (it may NOT be a placeholder). Each remaining element
  is either a fixed literal or a WHOLE-TOKEN placeholder ``"{param}"`` bound to a
  declared param. An embedded placeholder (``"--x={p}"``) is REJECTED — it must be
  split into two tokens — so a substituted value can never grow into a new flag.
* typed ``params``. At call time each ``{param}`` is replaced by exactly ONE argv
  element (the value coerced to ``str``). Because execution is
  ``create_subprocess_exec`` (``shell=False``), shell metacharacters inside a value
  are inert data — they can never become code.

``validate_spec`` is a structured, NEVER-raising gate the ``tool_build`` author flow
runs before persisting. ``build_argv`` is called inside the learned tool's
``execute`` and raises :class:`ToolSpecError` on a missing required arg — the caller
turns that into a failed ToolResult (never an unhandled raise).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stackowl.tools.knowledge.skill_validation import MAX_DESCRIPTION_LENGTH

__all__ = [
    "LearnedToolSpec",
    "ToolParam",
    "ToolSpecError",
    "build_argv",
    "validate_spec",
]

# Param names: lowercase identifier-like, mirrors the JSON-schema-friendly shape.
_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Tool names: same family as param names (kept local rather than reusing the skill
# name regex, which permits dots/hyphens that read oddly as a JSON-schema key).
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# A WHOLE-TOKEN placeholder: the entire argv element is exactly "{name}".
_WHOLE_PLACEHOLDER_RE = re.compile(r"^\{([a-z][a-z0-9_]*)\}$")
# Any "{...}" occurrence — used to detect EMBEDDED placeholders (non-whole-token).
_ANY_PLACEHOLDER_RE = re.compile(r"\{[a-z][a-z0-9_]*\}")

ParamType = Literal["string", "integer", "number", "boolean"]


class ToolSpecError(Exception):
    """Raised by :func:`build_argv` when a call cannot be turned into argv.

    Caught inside the learned tool's ``execute`` and surfaced as a failed
    ToolResult — never propagated as an unhandled exception.
    """


class ToolParam(BaseModel):
    """One declared parameter of a learned tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: ParamType
    description: str
    required: bool = False


class LearnedToolSpec(BaseModel):
    """A frozen, declarative spec for one agent-authored shell-backed tool.

    ``toolset_group`` is intentionally NOT author-controlled — the learned tool
    object pins it to ``"learned"``. There is likewise no ``consent_category``
    field: the author may not mint a dangerous consent category (which would let
    the model relax/raise its own gating).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_version: Literal[1] = 1
    name: str
    description: str
    params: list[ToolParam]
    argv_template: list[str]
    timeout_sec: float | None = None
    action_severity: Literal["read", "write", "consequential"] = "consequential"


def _declared_names(spec: LearnedToolSpec) -> list[str]:
    return [p.name for p in spec.params]


def validate_spec(spec: LearnedToolSpec) -> str | None:
    """Structured validation of a learned-tool spec. NEVER raises.

    Returns an error string to BLOCK with, or ``None`` if the spec is safe to
    register. Checks (in order): tool name, description length, param names
    (valid + unique), non-empty argv_template, argv[0] is a fixed literal,
    every placeholder is a WHOLE token, no embedded placeholders, every
    placeholder references a declared param.
    """
    # Tool name.
    if not _TOOL_NAME_RE.match(spec.name):
        return (
            f"Invalid tool name '{spec.name}'. Use lowercase letters, digits and "
            "underscores, starting with a letter (^[a-z][a-z0-9_]*$)."
        )
    # Description length.
    if len(spec.description) > MAX_DESCRIPTION_LENGTH:
        return (
            f"Description is {len(spec.description)} characters "
            f"(limit: {MAX_DESCRIPTION_LENGTH})."
        )
    # Param names: valid + unique. (Pydantic already validated the type literal.)
    declared = _declared_names(spec)
    seen: set[str] = set()
    for name in declared:
        if not _PARAM_NAME_RE.match(name):
            return (
                f"Invalid param name '{name}'. Use lowercase letters, digits and "
                "underscores, starting with a letter."
            )
        if name in seen:
            return f"Duplicate param name '{name}'. Each param must be unique."
        seen.add(name)

    # argv_template must be non-empty.
    if not spec.argv_template:
        return "argv_template must not be empty (argv[0] is the program to run)."

    # argv[0] must be a FIXED literal — no placeholder anywhere in it.
    if _ANY_PLACEHOLDER_RE.search(spec.argv_template[0]):
        return (
            "argv[0] (the program name) must be a fixed literal and may not "
            f"contain a placeholder; got '{spec.argv_template[0]}'."
        )

    declared_set = set(declared)
    for element in spec.argv_template[1:]:
        whole = _WHOLE_PLACEHOLDER_RE.match(element)
        if whole is not None:
            ref = whole.group(1)
            if ref not in declared_set:
                return (
                    f"argv token '{element}' references undeclared param '{ref}'. "
                    "Declare it in params or remove the placeholder."
                )
            continue
        # Not a whole-token placeholder — but it must contain NO placeholder at
        # all (an embedded "--x={p}" is rejected: it must be two tokens).
        if _ANY_PLACEHOLDER_RE.search(element):
            return (
                f"argv token '{element}' embeds a placeholder. A placeholder must "
                "be a WHOLE argv token (e.g. use two tokens '--x' and '{p}', not "
                "'--x={p}'), so a substituted value can never become a new flag."
            )
    return None


def build_argv(spec: LearnedToolSpec, call_args: dict[str, object]) -> list[str]:
    """Substitute call args into the argv template, returning the concrete argv.

    Each WHOLE-TOKEN placeholder ``"{param}"`` is replaced by exactly ONE argv
    element: ``str(value)``. A required param with no supplied value raises
    :class:`ToolSpecError`. An optional param with no value drops its token (so
    no empty/noise element is injected). Fixed literals pass through verbatim.

    Assumes ``spec`` already passed :func:`validate_spec` (argv[0] literal,
    whole-token placeholders, declared refs) — it does NOT re-validate shape; it
    only resolves values. ``shell=False`` execution makes every produced element
    inert data.
    """
    required = {p.name for p in spec.params if p.required}
    argv: list[str] = []
    for element in spec.argv_template:
        whole = _WHOLE_PLACEHOLDER_RE.match(element)
        if whole is None:
            argv.append(element)
            continue
        ref = whole.group(1)
        if ref not in call_args or call_args[ref] is None:
            if ref in required:
                raise ToolSpecError(f"missing required argument '{ref}'")
            # Optional + absent → drop the token entirely.
            continue
        argv.append(str(call_args[ref]))
    return argv
