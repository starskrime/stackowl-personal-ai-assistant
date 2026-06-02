"""image_generate — generate an image from a prompt on the LOCAL-FIRST substrate (E10-S4).

Prompt → an image FILE. The tool returns a PATH (under ``media_dir()`` — a
``send_file``-deliverable location), NEVER raw bytes: the agent reasons about the
path and ``send_file`` delivers it.

Self-hosted-first: the :class:`ImageSelector` prefers a local OSS image model, but
ONLY where a capability probe clears (x86 + CUDA + enough memory/disk). On an
incapable host (e.g. Tegra/unified-memory) the probe says no and a cloud backend
is used IF it is explicitly enabled + configured — and when it IS used the result
discloses EGRESS (the prompt left the machine) plus a cost note, mirroring the tts
/ vision egress precedent. If neither is available the tool returns an HONEST,
actionable "unavailable".

Self-healing / no-hidden-errors (B5): a failed probe, a skipped install, no
backend, or a generation error ALL degrade to a STRUCTURED ``ToolResult`` (logged)
— the tool NEVER raises. Severity ``read`` (no host side-effects beyond writing
into the media dir; cloud egress is disclosed in-output). Group ``media``.

Sensitive-data (B5): only the prompt LENGTH + the chosen size + backend are logged
— never the full prompt (image prompts can be sensitive) and never the image bytes.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.media.image.base import ImageResult
from stackowl.media.image.selector import ImageSelector
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_TOOLSET_GROUP = "media"


class ImageGenerateArgs(BaseModel):
    """Validated arguments for one ``image_generate`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str
    size: str | None = None


class ImageGenerateTool(Tool):
    """Generate an image from a text prompt and return the image file PATH.

    Local-first: runs on a local OSS image model where the capability probe clears
    (the prompt stays on the box); on an incapable host it falls back to a
    configured cloud engine and the result discloses that the prompt left the
    machine (plus a cost note). Read-only; degrades to a structured result, never
    raises.
    """

    def __init__(self, *, selector: ImageSelector | None = None) -> None:
        # Selector is injectable so a test can supply fake backends; the default is
        # built lazily from Settings().image at execute time (config-driven).
        self._selector = selector

    @property
    def name(self) -> str:
        return "image_generate"

    @property
    def description(self) -> str:
        return (
            "Generate an image from a text prompt and return the path to the "
            "generated image file (use send_file to deliver it). Runs on a LOCAL "
            "image model where the host supports it (the prompt stays on this "
            "machine); on an unsupported host it falls back to a configured cloud "
            "engine and the result discloses that the prompt left the machine. "
            "Read-only."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The text description of the image to generate.",
                },
                "size": {
                    "type": "string",
                    "description": (
                        "Optional output size as 'WIDTHxHEIGHT' (e.g. '1024x1024'). "
                        "Omit to use the configured default size."
                    ),
                },
            },
            "required": ["prompt"],
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

    def _build_selector(self) -> ImageSelector:
        if self._selector is not None:
            return self._selector
        from stackowl.config.settings import Settings

        return ImageSelector(Settings().image)

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY — log the prompt LENGTH only (never the prompt itself).
        t0 = time.monotonic()
        try:
            args = ImageGenerateArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError as exc:
            log.tool.warning(
                "image_generate.execute: invalid args",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {exc.error_count()} error(s)", t0)
        log.tool.info(
            "image_generate.execute: entry",
            extra={"_fields": {"prompt_len": len(args.prompt), "has_size": bool(args.size)}},
        )
        if not args.prompt or not args.prompt.strip():
            return self._err("missing required arg: prompt", t0)

        # 2. DECISION — select a backend LOCAL-FIRST; actionable if none (B5).
        try:
            selector = self._build_selector()
        except Exception as exc:  # bad settings must not crash the tool (B5).
            log.tool.error("image_generate.execute: selector build failed", exc_info=exc)
            return self._err(f"image substrate unavailable ({type(exc).__name__})", t0)
        selection = await selector.select()
        if not selection.available or selection.backend is None:
            reason = selection.reason or "image generation unavailable — no backend is available."
            log.tool.info("image_generate.execute: no backend — actionable result")
            return self._err(reason, t0)

        backend = selection.backend
        log.tool.info(
            "image_generate.execute: selected backend",
            extra={"_fields": {"backend": backend.name, "local": backend.is_local}},
        )

        # 3. STEP — generate. The backend returns a PATH (ImageResult) or a str error.
        outcome = await backend.generate(args.prompt, size=args.size)
        if isinstance(outcome, str):
            log.tool.info(
                "image_generate.execute: generation returned structured error",
                extra={"_fields": {"backend": backend.name}},
            )
            return self._err(outcome, t0)

        # 4. EXIT — disclose egress + cost IFF the backend is cloud (prompt left the box).
        return self._ok(outcome, t0)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _egress_header(backend_name: str) -> str:
        """The cloud-egress + cost disclosure prepended to the output."""
        return (
            f"[Cloud image generation: generated via the '{backend_name}' image "
            f"endpoint. The prompt was sent to that endpoint (it left this machine; "
            f"the local image model was unavailable). This is a paid/metered cloud "
            f"call and may incur a cost.]\n"
        )

    def _ok(self, result: ImageResult, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        # The human-facing output names the artifact PATH (send_file delivers it),
        # the size + the backend, and discloses egress+cost when the backend is cloud.
        lines = [
            f"Generated image saved to: {result.path}",
            f"size={result.size} backend={result.backend}",
        ]
        output = "\n".join(lines)
        if not result.is_local:
            output = self._egress_header(result.backend) + output
            log.tool.info(
                "image_generate.execute: CLOUD backend — egress + cost disclosed",
                extra={"_fields": {"backend": result.backend}},
            )
        log.tool.info(
            "image_generate.execute: exit",
            extra={"_fields": {
                "success": True, "backend": result.backend, "local": result.is_local,
                "size": result.size, "duration_ms": duration_ms,
            }},
        )
        return ToolResult(success=True, output=output, error=None, duration_ms=duration_ms)

    def _err(self, msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "image_generate.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
