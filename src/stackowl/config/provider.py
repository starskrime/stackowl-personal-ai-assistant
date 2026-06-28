"""ProviderConfig — one AI provider entry from stackowl.yaml."""

from typing import Literal

from pydantic import BaseModel

from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS


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
    # Generous fallback output budget used only when the model's real context
    # window isn't resolved (the primary path sizes output from the live window).
    # NOT a small cap: a reasoning model (thinking always on) must have room to
    # think AND emit its answer/JSON — a tight cap truncated the judge mid-thought,
    # producing empty verdicts. Output is free on a local model.
    max_output_tokens: int = 131072
    # F028/REACT-2 — the provider's own tool-loop ceiling. Derived from the default
    # per-turn step backstop (authz/bounds.DEFAULT_TURN_MAX_STEPS) so the two bounds
    # AGREE by construction: on the no-explicit-caps path the BudgetGovernor cuts at
    # DEFAULT_TURN_MAX_STEPS, and the provider's own ceiling sits at the SAME value
    # instead of 10 higher (which let the loop ceiling silently become the bound and
    # an uncounted wrap-up generation run as a 21st+ step). An owl with explicit caps
    # still overrides via max_iterations at the call site.
    tool_max_iterations: int = DEFAULT_TURN_MAX_STEPS
    # Optional per-model context window in CHARACTERS. When set, the tool loop uses
    # ~80% of this as its total-context trim budget instead of the global default,
    # so a small-context model gets trimmed sooner and a large one less aggressively.
    context_chars: int | None = None
    # Whether the endpoint supports NATIVE tool-calling (`tools=[...]` →
    # `response.tool_calls`). True for every modern OpenAI-compatible / Ollama
    # endpoint, so it is the default. When True we do NOT inject the text "ACTION:"
    # tool catalog into the prompt — injecting it competes with native tool_calls
    # and makes capable models emit a bare-JSON call as message *content* that the
    # text parser can't dispatch (the call is then bounced and the turn fails). The
    # text-protocol parser still runs as a fallback if a native call is ever absent.
    # Set False only for a legacy endpoint that genuinely lacks native tool-calling.
    supports_native_tools: bool = True
