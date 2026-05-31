"""Graceful max-out wrap-up directive (Phase F).

When the tool-use loop exhausts its iteration budget mid-task, the providers make
ONE final model call WITHOUT tools, after appending this directive as a user turn.
The goal: the user always receives a coherent, language-appropriate answer (best
result so far + remaining blocker + concrete next step) instead of silence.

Global and model-agnostic — no case specifics, no English-only assumptions about
the task (the model answers in the user's own language using the gathered context).
"""

from __future__ import annotations

WRAPUP_DIRECTIVE = (
    "You are out of tool-use steps for this turn. Do not call any tool. Using everything "
    "you have gathered so far, deliver your best, complete answer to the user now — include "
    "any result or file you produced. If the task is not fully done, clearly state what you "
    "accomplished, the specific remaining blocker, and the concrete next step."
)
