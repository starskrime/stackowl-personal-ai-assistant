"""Static description + JSON-Schema for the ``process`` tool (B2 split).

Kept out of :mod:`stackowl.tools.process.process_tool` so the tool file stays
under the B2 ≤300-line ceiling. No vendor names — the action surface mirrors the
prior-art supervised-process tool described neutrally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import ValidationError


def sanitized_errors(exc: ValidationError) -> list[dict[str, object]]:
    """Pydantic validation errors WITHOUT the raw ``input`` value.

    Pydantic's ``errors()`` echoes the rejected input, which for ``process`` can
    carry stdin ``data``/``line`` or ``env`` values — never log/return those.
    Surfaces only field + type + message.
    """
    return [
        {
            "field": ".".join(str(p) for p in e.get("loc", ())),
            "type": e.get("type"),
            "msg": e.get("msg"),
        }
        for e in exc.errors()
    ]


# The eight action verbs of the single action-discriminated ``process`` tool.
PROCESS_ACTIONS: tuple[str, ...] = (
    "start",
    "poll",
    "log",
    "write",
    "submit",
    "kill",
    "close",
    "list",
)

PROCESS_DESCRIPTION = (
    "Run and supervise a LONG-RUNNING / interactive OS process in the BACKGROUND "
    "(a dev server, a build, a REPL, a watcher). Action-discriminated; pass "
    "'action' plus that action's args.\n"
    "- start: launch a process. 'command' is an ARGV LIST (e.g. "
    "[\"python\",\"-m\",\"http.server\"]) — never a shell string; optional 'cwd', "
    "'env'. NON-BLOCKING: returns IMMEDIATELY with a 'process_id' while the process "
    "keeps running. Use the OTHER actions to interact with it by that id.\n"
    "- poll: get current status/exit_code without blocking.\n"
    "- log: read captured stdout/stderr ('stream'=stdout|stderr|both, optional "
    "'tail' max bytes).\n"
    "- write: send raw bytes to stdin ('data'). - submit: write one line ('line') "
    "+ newline to a prompt/REPL. - close: close stdin (send EOF). - kill: terminate "
    "(always allowed). - list: your running processes ('all'=true audits a "
    "cross-session view).\n"
    "TO AWAIT COMPLETION do NOT busy-loop calling poll — use the 'wait' tool with a "
    "predicate; it blocks efficiently until the process exits or output appears. "
    "LANE: anything that outlives one turn or needs stdin. ANTI-LANE: a quick "
    "one-shot command that finishes immediately — use 'shell' instead."
)

PROCESS_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(PROCESS_ACTIONS)},
        "command": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ARGV list (start). e.g. [\"npm\",\"run\",\"dev\"]. NOT a shell string.",
        },
        "cwd": {"type": "string", "description": "Working directory for 'start' (optional)."},
        "env": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Extra environment variables for 'start' (optional).",
        },
        "process_id": {
            "type": "string",
            "description": "Target process (poll/log/write/submit/kill/close).",
        },
        "stream": {
            "type": "string",
            "enum": ["stdout", "stderr", "both"],
            "description": "Which stream to read for 'log' (default both).",
        },
        "tail": {
            "type": "integer",
            "description": "For 'log': return at most this many trailing bytes per stream.",
        },
        "data": {"type": "string", "description": "Raw text to write to stdin ('write')."},
        "line": {"type": "string", "description": "A line to submit to stdin; a newline is appended ('submit')."},
        "all": {
            "type": "boolean",
            "description": "For 'list': true returns an audited cross-session view (default false).",
        },
    },
    "required": ["action"],
}
