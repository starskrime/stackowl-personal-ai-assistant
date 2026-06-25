"""Objective Manager — standing multi-turn objectives.

An objective is a persistent intent the assistant decomposes into ordered
sub-goals and works across many autonomous turns (driven by the scheduler)
until it is done or blocked on an irreversible decision. This is the keystone
that turns StackOwl from a single-turn agent into agentic AI: goals that are
held, decomposed, and followed through without the user re-asking.
"""

from __future__ import annotations

from stackowl.objectives.model import (
    Objective,
    ObjectiveEvent,
    ObjectiveStatus,
    Subgoal,
    SubgoalStatus,
)
from stackowl.objectives.store import ObjectiveStore

__all__ = [
    "Objective",
    "ObjectiveEvent",
    "ObjectiveStatus",
    "ObjectiveStore",
    "Subgoal",
    "SubgoalStatus",
]
