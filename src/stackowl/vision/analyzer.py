"""analyze_image_bytes — the shared vision-analysis core (E10-S2 / E10-S5).

Given ALREADY-LOADED, validated image bytes + their MIME, this:

* picks a vision-capable provider LOCAL-FIRST via :class:`VisionSelector` (the
  image stays on the box whenever a local vision model is configured); when none
  qualifies it returns a structured "no vision provider" outcome (no backend hit);
* calls the chosen provider's ``complete()`` with a :class:`Message` carrying the
  image as a ``DocumentBlock`` (``media_type=image/*``);
* on a CLOUD backend PREPENDS the egress-disclosure header (the image left the
  box), mirroring the pdf Mode B precedent; a LOCAL backend adds NO header.

It is the single place the select→DocumentBlock→complete→disclose flow lives, so
``vision_analyze`` (loads a workspace path / URL first) and ``browser_vision``
(feeds trusted screenshot bytes captured in-process) share IDENTICAL analysis +
disclosure behavior. The two tools differ ONLY in how they obtain the bytes.

Self-healing / no-hidden-errors (B5): a missing registry, no vision provider, or a
provider that raises ALL become a structured :class:`VisionAnalysis` (logged) — it
NEVER raises. Sensitive-data: only the image SIZE + MIME and the backend NAME are
logged; never the image bytes; the question is logged by LENGTH only.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.providers.base import DocumentBlock, Message
from stackowl.providers.registry import ProviderRegistry
from stackowl.vision.selector import VisionSelector

__all__ = ["VisionAnalysis", "analyze_image_bytes", "egress_header"]


@dataclass(frozen=True)
class VisionAnalysis:
    """The outcome of one image analysis.

    On success ``description`` is the human-facing answer (already egress-prefixed
    when the backend was cloud), ``backend`` names the provider, ``is_local`` tells
    whether the image stayed on-box. On failure ``error`` carries an actionable
    message and ``description``/``backend`` are empty.
    """

    success: bool
    description: str
    backend: str | None
    is_local: bool
    error: str | None

    @classmethod
    def ok(cls, description: str, *, backend: str, is_local: bool) -> VisionAnalysis:
        return cls(success=True, description=description, backend=backend, is_local=is_local, error=None)

    @classmethod
    def failed(cls, reason: str) -> VisionAnalysis:
        return cls(success=False, description="", backend=None, is_local=False, error=reason)


def egress_header(provider_name: str) -> str:
    """The cloud-egress disclosure prepended to the output (mirrors pdf Mode B)."""
    return (
        f"[Cloud vision: analyzed via vision-capable provider '{provider_name}'. "
        f"The image bytes were sent to that provider (it left this machine; no "
        f"local vision model was available).]\n"
    )


async def analyze_image_bytes(
    registry: ProviderRegistry | None,
    *,
    data: bytes,
    media_type: str,
    question: str,
) -> VisionAnalysis:
    """Analyze already-loaded image bytes; select→DocumentBlock→complete→disclose."""
    # 1. ENTRY — log the question LENGTH only (never the image bytes).
    log.tool.debug(
        "[vision_analyzer] analyze: entry",
        extra={"_fields": {"size": len(data), "mime": media_type, "question_len": len(question)}},
    )

    # 2. DECISION — resolve the registry; self-heal if absent (B5).
    if registry is None:
        log.tool.warning("[vision_analyzer] analyze: no provider_registry wired — unavailable")
        return VisionAnalysis.failed("vision substrate unavailable (no provider registry configured)")

    # Select a vision-capable provider LOCAL-FIRST; actionable if none.
    selection = VisionSelector(registry).select()
    if not selection.available or selection.provider is None:
        reason = selection.reason or (
            "No vision-capable model is available. Install a local vision model "
            "(e.g. an Ollama llava / llama3.2-vision tag via `ollama pull llava`) "
            "or configure a vision-capable provider."
        )
        log.tool.info("[vision_analyzer] analyze: no vision provider — actionable result")
        return VisionAnalysis.failed(reason)

    provider = selection.provider
    log.tool.info(
        "[vision_analyzer] analyze: selected vision provider",
        extra={"_fields": {"provider": provider.name, "local": selection.is_local}},
    )

    # 3. STEP — call the vision model with the image carried as an image block.
    block = DocumentBlock(data=data, media_type=media_type)
    message = Message(role="user", content=question, documents=(block,))
    try:
        result = await provider.complete([message], model="")
    except Exception as exc:  # provider failure → structured result, never raise (B5)
        log.tool.error(
            "[vision_analyzer] analyze: vision provider call failed",
            exc_info=exc,
            extra={"_fields": {"provider": provider.name}},
        )
        return VisionAnalysis.failed(
            f"vision provider '{provider.name}' failed: {type(exc).__name__}: {exc}"
        )

    # 4. EXIT — disclose egress IFF the backend is cloud (image left the box).
    description = result.content
    output = description
    if not selection.is_local:
        output = egress_header(provider.name) + description
        log.tool.info(
            "[vision_analyzer] analyze: CLOUD backend — egress disclosed",
            extra={"_fields": {"provider": provider.name, "image_bytes": len(data)}},
        )
    log.tool.debug(
        "[vision_analyzer] analyze: exit",
        extra={"_fields": {"success": True, "backend": provider.name, "local": selection.is_local}},
    )
    return VisionAnalysis.ok(output, backend=provider.name, is_local=selection.is_local)
