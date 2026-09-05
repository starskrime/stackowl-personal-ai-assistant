"""D16.5 — an MCP server that connects must be observable in production.

THE DOCTRINE was already adopted: PROCESS.md's Footprint Ladder reads "extend
existing code -> CLI command + skill -> service-gated tool -> plugin -> **MCP
server** -> new core tool (last resort)". MCP is rung 5 and a new core tool is the
last resort, which is exactly what this item asked for. `mcp/` covers both
directions — `client.py` consumes servers, `server.py` + `tool_exposure.py` +
`sse_encoder.py` expose us as one.

WHAT MEASUREMENT FOUND. Across nine daily logs there are **zero** `mcp.*`
messages, against a control of 1,521 `transcript.record_turn: exit` lines in the
same files — so the instrument reads and MCP has never been exercised. No server
is configured.

AND IF ONE WERE, THE OPERATOR COULD NOT SEE IT. Of 117 log calls in the package,
**two were INFO** and 82 were DEBUG. Production runs at INFO, so the two questions
an operator asks after configuring a server —

    did it connect, and how many tools did it expose?
    which of them reached the tool registry?

— were answered only by `discover_tools: exit` and `register_server_tools: exit`,
both DEBUG, both invisible. That is the D08.1 failure at package scale: an
acceptance check whose only evidence line is DEBUG can never be closed by any
volume of traffic.

The entry lines stay DEBUG. What is raised is the pair that carries the COUNT,
because a count nobody can read is not a measurement.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_CLIENT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src" / "stackowl" / "mcp" / "client.py"
)

#: The lines that answer "is my server working?". They are EXIT lines carrying a
#: count, which is what makes them evidence rather than noise.
_MUST_BE_INFO = {
    "mcp.client.discover_tools: exit",
    "mcp.client.register_server_tools: exit",
    "mcp.client.call_tool: exit",
}


def _log_calls() -> dict[str, str]:
    """Map each logged message to the level it is logged at."""
    tree = ast.parse(_CLIENT.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "log"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        msg = node.args[0].value
        if isinstance(msg, str):
            out[msg] = node.func.attr
    return out


@pytest.mark.parametrize("message", sorted(_MUST_BE_INFO))
def test_the_lifecycle_answer_is_readable_in_production(message: str) -> None:
    levels = _log_calls()
    assert message in levels, f"{message!r} is gone — was it renamed without moving this?"
    assert levels[message] == "info", (
        f"{message!r} logs at {levels[message].upper()}. Production runs at INFO, so "
        "an operator who configures an MCP server cannot see whether it connected "
        "or what it contributed. A log line that is the evidence for a claim must "
        "be INFO."
    )


def test_the_entry_lines_stay_debug() -> None:
    """The control. Raising everything would be noise, not observability —
    117 log calls at INFO is a different failure from 2."""
    levels = _log_calls()
    entries = {m: lvl for m, lvl in levels.items() if m.endswith(": entry")}

    assert entries, "expected entry lines to exist"
    assert all(lvl == "debug" for lvl in entries.values()), (
        f"entry lines should stay DEBUG: "
        f"{ {m: l for m, l in entries.items() if l != 'debug'} }"
    )
