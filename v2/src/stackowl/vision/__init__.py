"""Vision substrate (E10-S1) — pick a vision-capable provider + load image bytes.

Thin layer over the existing provider roster (Fork A1): no new model runtime, no
new SSRF guard. The :class:`VisionSelector` picks a healthy vision-capable
provider LOCAL-FIRST (self-hosted Ollama before cloud); the :class:`ImageLoader`
resolves a local path or http(s) URL to validated image bytes (reusing the shared
``SsrfGuard``). The S2 ``vision_analyze`` tool composes these. By design (Fork A1)
this package reuses only the leaf path-security utilities (``path_guard``) from
``tools`` — a pure, cycle-free leaf — and imports nothing else from that layer.
"""

from __future__ import annotations

from stackowl.vision.loader import ImageLoader, LoadedImage, LoadError
from stackowl.vision.selector import VisionSelection, VisionSelector

__all__ = [
    "ImageLoader",
    "LoadError",
    "LoadedImage",
    "VisionSelection",
    "VisionSelector",
]
