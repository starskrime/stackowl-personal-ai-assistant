"""media toolset group — image/audio/video tools (E10).

Holds ``vision_analyze`` (E10-S2): describe/answer-a-question about an image
(local path or http(s) URL) on the LOCAL-FIRST vision substrate (E10-S1), and
``browser_vision`` (E10-S5): screenshot the current browser page and analyze it
on the same substrate.
"""

from __future__ import annotations

from stackowl.tools.media.browser_vision import BrowserVisionTool
from stackowl.tools.media.image_generate import ImageGenerateTool
from stackowl.tools.media.tts import TtsTool
from stackowl.tools.media.vision_analyze import VisionAnalyzeTool

__all__ = ["BrowserVisionTool", "ImageGenerateTool", "TtsTool", "VisionAnalyzeTool"]
