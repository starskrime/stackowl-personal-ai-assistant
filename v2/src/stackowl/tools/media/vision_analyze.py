"""vision_analyze — answer a question about an IMAGE on the vision substrate (E10-S2).

A single Question + Image → Answer tool (no inner tool loop). It composes the
three E10-S1 substrate pieces:

* ``ImageLoader`` resolves the ``image`` arg (a workspace-confined local path OR
  an http(s) URL behind the shared SsrfGuard) to validated image bytes + MIME —
  a bad input becomes a structured result, no backend is ever hit;
* ``VisionSelector`` picks a healthy vision-capable provider LOCAL-FIRST (the
  image stays on the box whenever a local vision model is configured); when none
  qualifies the tool returns an ACTIONABLE "install a local vision model" result;
* the chosen provider's ``complete()`` is called with a ``Message`` carrying the
  image as a ``DocumentBlock`` (``media_type=image/*``) + the user's question;
  ``providers._blocks`` serializes that into the provider's native image block.

EGRESS DISCLOSURE (mirrors pdf Mode B): when the selected backend is CLOUD
(``not selection.is_local``) the image bytes LEAVE the machine, so the human-facing
output is prefixed with a clear note naming the provider. A LOCAL backend stays
on-box → NO egress note.

Self-healing / no-hidden-errors (B5): a missing provider registry, a load error,
no vision provider, or a provider that raises ALL degrade to a STRUCTURED
``ToolResult`` (logged) — the tool NEVER raises. Severity ``read`` (no host
side-effects; cloud egress is disclosed in-output). ``toolset_group`` ``"media"``.

Sensitive-data (B5): only the image SIZE + MIME and the backend NAME are logged —
never the image bytes; the question is logged by LENGTH only.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.vision.analyzer import analyze_image_bytes
from stackowl.vision.loader import ImageLoader, LoadedImage

_TOOLSET_GROUP = "media"
_DEFAULT_QUESTION = "Describe this image in detail."


class VisionAnalyzeArgs(BaseModel):
    """Validated arguments for one ``vision_analyze`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    image: str
    question: str = _DEFAULT_QUESTION


class VisionAnalyzeTool(Tool):
    """Describe / answer a question about an image (local path or http(s) URL).

    Local-first: the image stays on the box when a local vision model is
    configured; a cloud backend is disclosed in the output (the image leaves the
    machine). Read-only; degrades to a structured result, never raises.
    """

    def __init__(self, *, loader: ImageLoader | None = None) -> None:
        # Loader is injectable so a test can supply a deterministic SsrfGuard; the
        # default is the shared production loader (fails closed on private targets).
        self._loader = loader or ImageLoader()

    @property
    def name(self) -> str:
        return "vision_analyze"

    @property
    def description(self) -> str:
        return (
            "Analyze an image and answer a question about it (e.g. 'what is in this "
            "picture?'). Accepts a workspace-relative/absolute local path OR an "
            "http(s) image URL. Runs on a LOCAL vision model when one is configured "
            "(the image stays on this machine); if it falls back to a cloud "
            "vision model the result discloses that the image left the machine. "
            "Read-only."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": (
                        "The image to analyze: a local file path (relative to the "
                        "workspace or absolute inside it) OR an http(s) URL."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": (
                        "What to ask about the image. Defaults to a full "
                        "description."
                    ),
                    "default": _DEFAULT_QUESTION,
                },
            },
            "required": ["image"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY — log the question LENGTH only (never the image bytes).
        t0 = time.monotonic()
        try:
            args = VisionAnalyzeArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError as exc:
            log.tool.warning(
                "vision_analyze.execute: invalid args",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {exc.error_count()} error(s)", t0)
        log.tool.info(
            "vision_analyze.execute: entry",
            extra={"_fields": {"has_image": bool(args.image), "question_len": len(args.question)}},
        )
        if not args.image or not args.image.strip():
            return self._err("missing required arg: image", t0)

        # 2. STEP — load the image FIRST (a bad input never reaches a backend).
        loaded = await self._loader.load(args.image)
        if not isinstance(loaded, LoadedImage):
            log.tool.info(
                "vision_analyze.execute: image load failed — structured result",
                extra={"_fields": {"reason": loaded.reason}},
            )
            return self._err(f"could not load image: {loaded.reason}", t0)
        log.tool.debug(
            "vision_analyze.execute: image loaded",
            extra={"_fields": {"size": loaded.size, "mime": loaded.media_type}},
        )

        # 3-5. Analyze on the SHARED vision core (select→DocumentBlock→complete→
        # egress-disclose). Identical behavior to browser_vision; the two tools
        # differ ONLY in how they obtain the bytes (this one loaded a path/URL).
        analysis = await analyze_image_bytes(
            get_services().provider_registry,
            data=loaded.data,
            media_type=loaded.media_type,
            question=args.question,
        )
        if not analysis.success:
            return self._err(analysis.error or "vision analysis failed", t0)
        return self._ok(
            analysis.description, t0, backend=analysis.backend or "", local=analysis.is_local
        )

    # ---------------------------------------------------------------- helpers
    def _ok(self, description: str, t0: float, *, backend: str, local: bool) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "vision_analyze.execute: exit",
            extra={"_fields": {
                "success": True, "backend": backend, "local": local,
                "output_len": len(description), "duration_ms": duration_ms,
            }},
        )
        # ``description`` is the human-facing answer (already egress-prefixed when
        # cloud); ``backend``/``local`` are surfaced for the model's own awareness.
        return ToolResult(success=True, output=description, error=None, duration_ms=duration_ms)

    def _err(self, msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "vision_analyze.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
