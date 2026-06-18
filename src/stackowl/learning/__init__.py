"""Cross-subsystem learning surface (Learning Commit 5).

Federates reflections + skills + tool_heuristics + knowledge_pellets into a
single LanceDB-backed semantic index, so any subsystem (tools, parliament,
classify) can query for relevant prior learning with one ANN call.

Per [[feedback_use_existing_infrastructure]]: lessons live in the same
LanceDB connection that already serves committed_facts (different table),
not in a Python-level aggregator over per-source SQLite stores.
"""

from stackowl.learning.lesson import Lesson, LessonHit, LessonSource

__all__ = ["Lesson", "LessonHit", "LessonSource"]
