"""Live turn-progress — warm, ephemeral "what I'm doing now" status.

The emitter (``emitter.py``) observes the ReAct loop and produces normalized
progress facts; the vocabulary (``vocabulary.py``) renders them into friendly,
localized, internal-free phrases shared by every channel. Telegram renders them
as a mutating status message (``channels/telegram/progress_render.py``); the
terminal TUI renders them via the existing ``PipelineStrip`` widget.
"""

from __future__ import annotations

from stackowl.pipeline.progress.vocabulary import ProgressKey, coerce_key, render

__all__ = ["ProgressKey", "coerce_key", "render"]
