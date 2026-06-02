"""media toolset group — image/audio/video tools (E10).

Currently holds ``vision_analyze`` (E10-S2): describe/answer-a-question about an
image (local path or http(s) URL) on the LOCAL-FIRST vision substrate (E10-S1).
"""

from __future__ import annotations

from stackowl.tools.media.vision_analyze import VisionAnalyzeTool

__all__ = ["VisionAnalyzeTool"]
