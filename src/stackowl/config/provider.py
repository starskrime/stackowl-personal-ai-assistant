"""ProviderConfig — one AI provider entry from stackowl.yaml."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS


class ModelOverride(BaseModel):
    """An additional model served by the SAME provider connection (api_key/
    base_url/protocol) as its parent ``ProviderConfig`` — lets one connection
    host multiple models, each independently tier-routable and independently
    able to override its context/output-token budget, without duplicating
    the whole provider block for a second model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tiers: tuple[Literal["fast", "standard", "powerful", "local"], ...]
    # Mirrors ProviderConfig.enabled — disabling this model entry leaves its
    # `tiers` intact but makes it unroutable (ProviderRegistry skips it when
    # building ModelRoutes), the SAME "always routable-or-explicitly-off"
    # invariant a disabled provider already gets, at model granularity. This
    # is what lets `/tier remove`'s model-scoped 3-arg form DISABLE a model's
    # last remaining tier instead of deleting the models[] entry outright.
    enabled: bool = True
    # None = inherit the parent ProviderConfig's own value for this field.
    max_output_tokens: int | None = None
    context_chars: int | None = None
    # Whether THIS model reads images. None = inherit the parent. One connection can
    # front both a text model and a multimodal one, and they need not agree.
    supports_vision: bool | None = None

    @field_validator("tiers")
    @classmethod
    def _validate_tiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("a model's tiers must contain at least one entry")
        if len(set(value)) != len(value):
            raise ValueError(f"a model's tiers must not contain duplicates: {value}")
        return value


class ProviderConfig(BaseModel):
    """Configuration for one AI provider.

    Ollama and any OpenAI-compatible provider (Groq, Together, Mistral, etc.)
    use ``protocol: openai`` with a custom ``base_url``; no new protocol type
    is ever needed for a new provider.
    """

    # NOT extra="forbid", and the difference from ModelOverride above is
    # deliberate rather than an oversight. Forbidding is the stricter fix and it
    # is right for a config we control; this one is hand-edited YAML in
    # deployments we cannot see, and making a previously-accepted file fail to
    # boot on upgrade is a product decision, not a bug fix. So an unknown key is
    # ANNOUNCED and the platform keeps booting. Escalated as E-provider-strict.
    #
    # Why announcing matters at all: NINE of this model's seventeen fields are
    # unreachable from any `/provider` command, so the fields most likely to be
    # typed by hand are exactly the ones whose typos vanish — and every one of
    # them describes what a backend can DO. A dropped `supports_native_tools`
    # does not fail; it silently takes the other capability path.
    @model_validator(mode="before")
    @classmethod
    def _announce_unknown_keys(cls, data: object) -> object:
        """Say so when a key was typed and will be ignored. Never raises."""
        try:
            if not isinstance(data, dict):
                return data
            unknown = sorted(set(data) - set(cls.model_fields))
            if unknown:
                from stackowl.infra.observability import log

                log.config.warning(
                    "[config] provider entry has unknown keys — they are IGNORED, so "
                    "whatever they were meant to set is not in effect",
                    extra={"_fields": {
                        "provider": str(data.get("name") or "<unnamed>"),
                        "unknown_keys": unknown,
                        "known_keys": sorted(cls.model_fields),
                    }},
                )
        except Exception as exc:  # never let bookkeeping block a config load
            from stackowl.infra.observability import log

            log.config.error(
                "[config] could not check a provider entry for unknown keys",
                exc_info=exc,
            )
        return data

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
    # Additional models sharing THIS provider's connection (api_key/base_url/
    # protocol) — each independently tier-routable, each able to override its
    # own context/output-token budget. Empty by default: every existing
    # single-model config is completely unaffected.
    models: tuple[ModelOverride, ...] = ()
    max_retries: int = 3
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
    # WHETHER THIS BACKEND READS IMAGES — declared, because it cannot be inferred.
    #
    # None = fall through to `providers.vision_models.is_vision_model`, the 33-token
    # vendor-substring heuristic, so every existing deployment is unchanged.
    #
    # MEASURED 2026-08-20: that heuristic recognised 0 of 99,573 recorded calls across
    # all 8 models this deployment has ever run. Its list carries `gemma3` while the box
    # runs gemma4, and `qwen2-vl` while the box runs qwen3.5/3.6 — and the primary
    # backend is a private gateway model named `neraai-v1-raw`, which no vendor list can
    # ever describe. Vision was not degraded, it was unreachable: VisionSelector could
    # never return a provider, taking vision_analyze, browser_vision and GUI vision
    # routing with it. The image TRANSPORT was fine the whole time.
    #
    # This does not contradict the standing "prefer dynamic discovery" rule. That rule
    # forbids guessing what the system KNOWS; a substring list is not discovery, it is a
    # hardcoded guess wearing discovery's clothes, and it breaks two other standing rules
    # outright (no hardcoded keyword lists, no vendor names in src/). A private model's
    # capabilities are not derivable from its name by anyone, which is precisely where a
    # declaration is more honest than an inference.
    #
    # Rung ONE of the ladder model_window.py already proves: override, then probe, then
    # catalog, then a conservative default. The heuristic stays as the last rung until
    # the probe exists — deleting it first would regress deployments it does describe.
    supports_vision: bool | None = None

    def resolve_vision(self, model: str | None = None) -> bool:
        """Whether ``model`` (default: this backend's own) can read images.

        Precedence: a per-model declaration, then this backend's declaration, then
        the name heuristic. Never raises — a capability check that throws would take
        the turn with it.
        """
        try:
            target = model or self.default_model
            for entry in self.models:
                if entry.name == target and entry.supports_vision is not None:
                    return entry.supports_vision
            if self.supports_vision is not None:
                return self.supports_vision
            from stackowl.providers.vision_models import is_vision_model

            return is_vision_model(target)
        except Exception as exc:  # pragma: no cover — defensive
            from stackowl.infra.observability import log

            log.config.error(
                "[config] could not resolve a backend's vision capability — "
                "reporting NOT vision-capable",
                exc_info=exc, extra={"_fields": {"provider": self.name}},
            )
            return False

    # `quirks` WAS HERE AND IS DELETED (2026-08-20, Bakir's call). D02.6 declared it
    # as the escape hatch that justified not porting the reference platform's
    # English-matching classifier: "every reason they encode is reachable from a
    # status code or a ProviderConfig.quirks entry instead". Measured 2026-08-20:
    # the field had ZERO readers in src/ — one grep hit, and it was a docstring. No
    # YAML ever set it, and its only test asserted that the constructor round-trips
    # it, which is green and proves nothing.
    #
    # Deleted rather than given a reader, because the reader was never possible: the
    # adapter that would interpret a quirk is the same classifier that promises
    # "structural classification only, no English matching", so a free-form token
    # has nowhere to be understood. Keeping it "pending a reader" is exactly how it
    # survived long enough to be found twice. `_resilient_round`'s docstring is
    # corrected in the same commit so it no longer promises this mechanism.
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
    # D01.2 — how long a prompt-cache entry lives on a provider that supports
    # cache breakpoints. Per-provider rather than global because the TTL is an
    # ECONOMIC decision about one backend: a user running two Anthropic backends
    # (a chatty one and a batch one) wants them priced differently.
    #
    # 5m is the default because a conversation is normally a burst of turns
    # minutes apart. 1h doubles the write cost (2x vs 1.25x) and needs 3+ reads
    # to break even, so a conversation that stops after two turns becomes MORE
    # expensive than not caching at all.
    #
    # Literal-typed, so a typo fails at config load rather than shipping a marker
    # the API rejects on the first real request. Inert on any provider whose
    # supports_cache_breakpoints is False, which is every provider but Anthropic.
    cache_ttl: Literal["5m", "1h"] = "5m"
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

    @field_validator("models")
    @classmethod
    def _validate_models(
        cls, value: tuple[ModelOverride, ...], info: ValidationInfo
    ) -> tuple[ModelOverride, ...]:
        names = [m.name for m in value]
        default = info.data.get("default_model")
        if default is not None and default in names:
            raise ValueError(f"model name '{default}' collides with default_model")
        if len(set(names)) != len(names):
            raise ValueError(f"models must not contain duplicate names: {names}")
        return value
