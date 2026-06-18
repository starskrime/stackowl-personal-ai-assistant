"""Agents toolset — multi-agent surface tools (E8).

Contains ``delegate_task`` (wraps the ``A2ADelegator`` Secretary→specialist
round-trip with depth/width safety rails) and ``mixture_of_agents`` (fans one
hard question across the healthy provider roster, then synthesizes via the
parliament synthesizer). Both return structured, self-healing result shapes.
"""

from __future__ import annotations

from stackowl.tools.agents.delegate_task import DelegateTaskTool
from stackowl.tools.agents.mixture_of_agents import MixtureOfAgentsTool

__all__ = ["DelegateTaskTool", "MixtureOfAgentsTool"]
