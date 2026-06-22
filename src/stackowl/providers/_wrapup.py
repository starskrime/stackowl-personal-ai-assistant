"""Graceful max-out wrap-up directive (Phase F).

When the tool-use loop exhausts its iteration budget mid-task, the providers make
ONE final model call WITHOUT tools, after appending this directive as a user turn.
The goal: the user always receives a coherent, language-appropriate answer (best
result so far + remaining blocker + concrete next step) instead of silence.

Global and model-agnostic — no case specifics, no English-only assumptions about
the task (the model answers in the user's own language using the gathered context).
"""

from __future__ import annotations

LOOP_REPEAT_DIRECTIVE = (
    "You have repeated the same tool call with identical arguments several times and it is not making progress. "
    "Do not repeat it again. Either try a materially different approach, or stop and deliver your best answer "
    "so far — including a clear statement of what you accomplished, the specific blocker, and the next concrete step."
)

WRAPUP_DIRECTIVE = (
    "You are out of tool-use steps for this turn. Do not call any tool. Using everything "
    "you have gathered so far, deliver your best, complete answer to the user now — include "
    "any result or file you produced. If the task is not fully done, clearly state what you "
    "accomplished, the specific remaining blocker, and the concrete next step."
)

# Injected when a model's "final answer" was actually an unparsed tool call (an
# ACTION block or a bare JSON object) — it tried to act but used the wrong syntax,
# so the call never ran and must NOT be shown to the user. Re-states the exact
# text-protocol format so the model can re-emit a parseable call.
FORMAT_FIX_DIRECTIVE = (
    "Your last message was a tool call written in the wrong format, so it did NOT run and was "
    "not shown to the user. To call a tool, reply with EXACTLY this shape:\n"
    "ACTION: <tool_name>\n"
    "```json\n"
    "{\"arg\": \"value\"}\n"
    "```\n"
    "Put ACTION: and the tool name on their own line, then the JSON arguments in a fenced "
    "```json block with REAL newlines (not the two characters backslash-n). "
    "If you instead have the final answer for the user, write it as plain prose with no JSON."
)
