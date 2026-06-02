"""Vision-capable model detection — a maintainable name-based signal (E10-S1).

The runtime ``ProviderConfig`` carries only ``default_model`` (no per-model
metadata), so "is this provider's configured model able to read images?" is
answered by matching the model name against a curated allow-list of known
vision/multimodal model FAMILIES. This is network-free (no probe), cross-platform,
and works whether or not a local model is actually installed — a provider simply
reports the capability its configured model is known to have.

The onboarding catalog (``setup/providers/*.yaml``) additionally carries a
``vision: true`` marker per model for the picker; this module is the *runtime*
counterpart that the providers consult on every construction.

Matching is substring-based and case-insensitive: a vision family token anywhere
in the model id flips the flag (e.g. ``llava``, ``llama3.2-vision``,
``gpt-4o``, ``claude-sonnet-4-6``, ``gemini-2.5-pro``). Extending support for a
new vision model is a one-line edit to ``_VISION_MODEL_TOKENS``.
"""

from __future__ import annotations

from stackowl.infra.observability import log

__all__ = ["is_vision_model"]

# Substring tokens (lower-cased) that mark a model as vision/multimodal-capable.
# Grouped by family for maintainability; order is irrelevant (any match wins).
_VISION_MODEL_TOKENS: frozenset[str] = frozenset(
    {
        # --- Local (Ollama / llama.cpp / lmstudio) multimodal families ---
        "llava",
        "bakllava",
        "llama3.2-vision",
        "llama-3.2-vision",
        "llama3.2vision",
        "vision",  # generic '-vision' tagged local tags (e.g. qwen2-vl style retags)
        "moondream",
        "minicpm-v",
        "minicpm-v2",
        "llama4",  # llama4 scout/maverick are natively multimodal
        "gemma3",  # gemma3 vision variants
        "qwen2-vl",
        "qwen2.5-vl",
        "qwen-vl",
        # --- Cloud OpenAI multimodal ---
        "gpt-4o",
        "gpt-4-turbo",
        "gpt-4-vision",
        "gpt-4.1",
        "gpt-5",
        "o3",
        "o4",
        # --- Cloud Anthropic (all Claude 3+ are vision-capable) ---
        "claude-3",
        "claude-sonnet-4",
        "claude-opus-4",
        "claude-haiku-4",
        "claude-3-5",
        "claude-3.5",
        "claude-3-7",
        "claude-3.7",
        # --- Cloud Google Gemini (1.5+ are multimodal) ---
        "gemini-1.5",
        "gemini-2",
        "gemini-pro-vision",
        "gemini-flash",
    }
)


def is_vision_model(model: str | None) -> bool:
    """Return True iff ``model`` names a known vision/multimodal-capable model.

    Defensive: a ``None``/empty/odd model id returns False (a provider then
    reports "not vision-capable" rather than raising). Never raises.
    """
    if not model:
        return False
    try:
        needle = model.strip().lower()
    except Exception as exc:  # pragma: no cover — model is a str; belt-and-braces
        log.engine.debug(
            "[vision_models] is_vision_model: bad model id — treating as non-vision",
            extra={"_fields": {"err": str(exc)}},
        )
        return False
    if not needle:
        return False
    matched = any(token in needle for token in _VISION_MODEL_TOKENS)
    log.engine.debug(
        "[vision_models] is_vision_model: classified",
        extra={"_fields": {"model": needle, "vision": matched}},
    )
    return matched
