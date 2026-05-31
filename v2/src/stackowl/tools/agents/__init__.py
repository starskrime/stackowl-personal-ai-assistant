"""Agents toolset — multi-agent surface tools (E8).

Contains the ``delegate_task`` tool, which wraps the existing ``A2ADelegator``
Secretary→specialist round-trip with the E8 safety rails (depth backstop +
per-trace width cap) and a structured, self-healing result shape.
"""

from __future__ import annotations

from stackowl.tools.agents.delegate_task import DelegateTaskTool

__all__ = ["DelegateTaskTool"]
