"""Cross-subsystem learning surface (Learning Commit 5).

Federates reflections + skills + tool_heuristics + knowledge_pellets into a
single semantic index (SQLite + numpy since D08.2), so any subsystem (tools, parliament,
classify) can query for relevant prior learning with one ANN call.

Per [[feedback_use_existing_infrastructure]]: lessons live in the same
SQLite database that already serves the rest of memory,
not in a Python-level aggregator over per-source SQLite stores.
"""

from stackowl.learning.lesson import Lesson, LessonHit, LessonSource

__all__ = ["Lesson", "LessonHit", "LessonSource"]
