"""ProviderConfig — one AI provider entry from stackowl.yaml."""

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

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
    # F-multi-tier — a provider can belong to more than one routing tier at
    # once (e.g. the same key serving both "fast" and "standard"). At least
    # one entry, no duplicates (enforced below). The legacy singular `tier`
    # constructor kwarg is still accepted (see the validator below) so the
    # ~50 existing call sites across this codebase that build
    # ProviderConfig(tier="fast", ...) keep working unchanged — none of them
    # read the removed `.tier` attribute back, only `.tiers`.
    tiers: tuple[Literal["fast", "standard", "powerful", "local"], ...]
    max_retries: int = 3
    timeout_seconds: float = 30.0
    rate_limit_rpm: int | None = None  # Requests per minute; None = no limit
    # Generous output budget — this is also the ceiling _output_cap() applies
    # against a resolved window (min(window, max_output_tokens)), not only the
    # fallback for an unresolved one. NOT a small cap: a reasoning model
    # (thinking always on) must have room to think AND emit its answer/JSON —
    # a tight cap truncated the judge mid-thought, producing empty verdicts.
    # 250000 (raised from 131072, 2026-07-21, explicit operator request): the
    # old value capped output at exactly half of a 262144-context model's
    # window even though real prompts run far smaller (~12K tokens observed),
    # needlessly leaving output room on the table. Output is free on a local
    # model — the remaining ~12K-token headroom below the window still covers
    # observed prompt sizes.
    max_output_tokens: int = 250000
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
    # F-quota — hours to keep this provider's circuit OPEN after a quota/rate
    # failure with NO parseable reset signal from the provider's own response
    # (e.g. "I know this free tier resets daily"). None (default): no change
    # from today's generic failure-threshold breaker behavior. See
    # providers/_resilient_round.py's RATE_LIMIT branch for how this is used.
    cooldown_hours: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_tier(cls, data: object) -> object:
        """Accept a legacy singular ``tier=<str>`` constructor kwarg (or dict
        key) as an alias for ``tiers=(<str>,)``. Runs BEFORE field validation,
        so a caller that still passes ``tier="fast"`` — whether constructing
        ProviderConfig directly in Python or via a raw YAML/dict that hasn't
        been through the on-disk migration yet — is normalized here rather
        than rejected. ``tiers`` wins if both are somehow present."""
        if not isinstance(data, dict) or "tiers" in data or "tier" not in data:
            return data
        legacy = data.pop("tier")
        data["tiers"] = (legacy,) if isinstance(legacy, str) else tuple(legacy)
        return data

    @field_validator("tiers")
    @classmethod
    def _validate_tiers(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("tiers must contain at least one entry")
        if len(set(value)) != len(value):
            raise ValueError(f"tiers must not contain duplicates: {value}")
        return value
