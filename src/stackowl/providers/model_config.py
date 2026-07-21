"""resolve_model_override — per-model context/output-token override lookup.

Shared by every ModelProvider subclass (OpenAI, Anthropic, Gemini) so a
provider serving multiple models (ProviderConfig.models) resolves each
model's OWN max_output_tokens/context_chars — falling back to the parent
provider's own value when the model has no override, or is the provider's
own default_model (never in .models).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stackowl.config.provider import ProviderConfig


def resolve_model_override(config: ProviderConfig, model_name: str) -> tuple[int, int | None]:
    """Return (effective_max_output_tokens, effective_context_chars) for ``model_name``.

    ``model_name`` matching an entry in ``config.models`` with a non-None
    override uses that model's own value; any other case (no match, or a
    match with the override field left None) falls back to the provider's
    own ``max_output_tokens``/``context_chars``.
    """
    for m in config.models:
        if m.name == model_name:
            max_tokens = m.max_output_tokens if m.max_output_tokens is not None else config.max_output_tokens
            context_chars = m.context_chars if m.context_chars is not None else config.context_chars
            return max_tokens, context_chars
    return config.max_output_tokens, config.context_chars
