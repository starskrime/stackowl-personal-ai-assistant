"""ProviderConfig — one AI provider entry from stackowl.yaml."""

from typing import Literal

from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """Configuration for one AI provider.

    Ollama and any OpenAI-compatible provider (Groq, Together, Mistral, etc.)
    use ``protocol: openai`` with a custom ``base_url``; no new protocol type
    is ever needed for a new provider.
    """

    name: str
    protocol: Literal["openai", "anthropic", "gemini", "grok"]
    enabled: bool = True
    api_key: str | None = None
    base_url: str | None = None
    default_model: str
    tier: Literal["fast", "standard", "powerful", "local"]
    max_retries: int = 3
    timeout_seconds: float = 30.0
    rate_limit_rpm: int | None = None  # Requests per minute; None = no limit
    max_output_tokens: int = 4096
    tool_max_iterations: int = 8
    # Optional per-model context window in CHARACTERS. When set, the tool loop uses
    # ~80% of this as its total-context trim budget instead of the global default,
    # so a small-context model gets trimmed sooner and a large one less aggressively.
    context_chars: int | None = None
