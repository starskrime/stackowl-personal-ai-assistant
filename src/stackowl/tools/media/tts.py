"""tts — synthesize speech from text on the LOCAL-FIRST TTS substrate (E10-S3).

Text → an audio FILE. The tool returns a PATH (under ``media_dir()`` — a
``send_file``-deliverable location), NEVER raw bytes: the agent reasons about the
path and ``send_file`` delivers it.

Self-hosted-first: the :class:`TtsSelector` picks the local OSS engine whenever it
can initialize (the text stays on the box). A cloud backend exists but is DISABLED
unless explicitly enabled + configured, and is only used as a fallback — and when
it IS used the result discloses EGRESS (the text left the machine), mirroring the
pdf Mode B / vision egress precedent.

Self-healing / no-hidden-errors (B5): a missing engine, an install failure, no
cloud configured, or a synthesis error ALL degrade to a STRUCTURED ``ToolResult``
(logged) — the tool NEVER raises. Severity ``read`` (no host side-effects beyond
writing into the media dir; cloud egress is disclosed in-output). Group ``media``.

Sensitive-data (B5): only the text LENGTH + the chosen voice + backend are logged
— never the full text (it may be sensitive) and never the audio bytes.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.media.tts.base import TtsResult
from stackowl.media.tts.selector import TtsSelector
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_TOOLSET_GROUP = "media"


class TtsArgs(BaseModel):
    """Validated arguments for one ``tts`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    voice: str | None = None


class TtsTool(Tool):
    """Convert text to spoken audio and return the audio file PATH.

    Local-first: runs on the local OSS TTS engine when available (the text stays
    on the box); if it falls back to a configured cloud engine the result
    discloses that the text left the machine. Read-only; degrades to a structured
    result, never raises.
    """

    def __init__(self, *, selector: TtsSelector | None = None) -> None:
        # Selector is injectable so a test can supply fake backends; the default is
        # built lazily from Settings().tts at execute time (config-driven).
        self._selector = selector

    @property
    def name(self) -> str:
        return "tts"

    @property
    def description(self) -> str:
        return (
            "Synthesize speech audio from text and return the path to the generated "
            "audio file (use send_file to deliver it). Runs on a LOCAL text-to-speech "
            "engine by default (the text stays on this machine); if it falls back to a "
            "configured cloud engine the result discloses that the text left the "
            "machine. Read-only."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to speak aloud.",
                },
                "voice": {
                    "type": "string",
                    "description": (
                        "Optional voice id. Omit to use the configured default voice."
                    ),
                },
            },
            "required": ["text"],
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

    def _build_selector(self) -> TtsSelector:
        if self._selector is not None:
            return self._selector
        from stackowl.config.settings import Settings

        return TtsSelector(Settings().tts)

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY — log the text LENGTH only (never the text itself).
        t0 = time.monotonic()
        try:
            args = TtsArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError as exc:
            log.tool.warning(
                "tts.execute: invalid args",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {exc.error_count()} error(s)", t0)
        log.tool.info(
            "tts.execute: entry",
            extra={"_fields": {"text_len": len(args.text), "has_voice": bool(args.voice)}},
        )
        if not args.text or not args.text.strip():
            return self._err("missing required arg: text", t0)

        # 2. DECISION — select a backend LOCAL-FIRST; actionable if none (B5).
        try:
            selector = self._build_selector()
        except Exception as exc:  # bad settings must not crash the tool (B5).
            log.tool.error("tts.execute: selector build failed", exc_info=exc)
            return self._err(
                f"tts substrate unavailable ({type(exc).__name__})", t0
            )
        selection = await selector.select(voice=args.voice)
        if not selection.available or selection.backend is None:
            reason = selection.reason or "tts unavailable — no TTS backend is available."
            log.tool.info("tts.execute: no backend — actionable result")
            return self._err(reason, t0)

        backend = selection.backend
        log.tool.info(
            "tts.execute: selected backend",
            extra={"_fields": {"backend": backend.name, "local": backend.is_local}},
        )

        # 3. STEP — synthesize. The backend returns a PATH (TtsResult) or a str error.
        outcome = await backend.synthesize(args.text, voice=args.voice)
        if isinstance(outcome, str):
            log.tool.info(
                "tts.execute: synthesis returned structured error",
                extra={"_fields": {"backend": backend.name}},
            )
            return self._err(outcome, t0)

        # DEFENSIVE: a TtsResult OBJECT is the backend's self-report, not proof real
        # audio exists. Inspect the artifact INLINE — it must exist and be non-empty
        # — before self-asserting success (F-34). Only a POSITIVE absence
        # (verify_artifact is False) refuses; an unobservable path (None, transient FS
        # error) defers to verify()'s magic-byte seam downstream so a real success is
        # never flipped on an inability to observe.
        from stackowl.tools.verification import verify_artifact

        if verify_artifact(outcome.path) is False:
            log.tool.warning(
                "tts.execute: backend claimed success but the audio file is missing "
                "or empty",
                extra={"_fields": {"backend": backend.name}},
            )
            return self._err(
                "tts reported success but produced no readable audio file", t0
            )

        # 4. EXIT — disclose egress IFF the backend is cloud (text left the box).
        return self._ok(outcome, t0)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _egress_header(backend_name: str) -> str:
        """The cloud-egress disclosure prepended to the output (mirrors pdf Mode B)."""
        return (
            f"[Cloud TTS: synthesized via the '{backend_name}' speech endpoint. The "
            f"text was sent to that endpoint (it left this machine; the local TTS "
            f"engine was unavailable).]\n"
        )

    def _ok(self, result: TtsResult, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        # The human-facing output names the artifact PATH (send_file delivers it),
        # the voice + the backend, and discloses egress when the backend is cloud.
        lines = [
            f"Synthesized speech saved to: {result.path}",
            f"voice={result.voice} backend={result.backend} "
            f"duration_ms={result.duration_ms:.0f}",
        ]
        output = "\n".join(lines)
        if not result.is_local:
            output = self._egress_header(result.backend) + output
            log.tool.info(
                "tts.execute: CLOUD backend — egress disclosed",
                extra={"_fields": {"backend": result.backend}},
            )
        log.tool.info(
            "tts.execute: exit",
            extra={"_fields": {
                "success": True, "backend": result.backend, "local": result.is_local,
                "voice": result.voice, "duration_ms": duration_ms,
            }},
        )
        return ToolResult(
            success=True, output=output, error=None, duration_ms=duration_ms,
            artifact_path=str(result.path),  # structured locator for verify()
        )

    async def verify(
        self, args: dict[str, object], result: ToolResult, *, started_at: float
    ) -> bool | None:
        """Post-condition: the synthesized audio exists, is non-empty, is this run's
        artifact (fresh), and has a real audio header."""
        from stackowl.tools.verification import verify_artifact

        return verify_artifact(
            result.artifact_path, not_before=started_at, expect_kind="audio"
        )

    def _err(self, msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "tts.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
