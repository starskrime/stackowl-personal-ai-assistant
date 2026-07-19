# NeraAiRaw gateway: native tool-calling not reaching the API's structured field

**Status:** tracked external dependency — cannot be fixed from this repo.
**Owner:** whoever operates `llm-gateway.dev.nera.gov`.
**Filed:** 2026-07-16, after a live incident where a model's tool-call attempt
streamed to a user as raw garbage for 36 seconds before this app's safety net
caught it.

## Observation

The `NeraAiRaw` provider (model `neraai-v1-raw`, served from
`http://llm-gateway.dev.nera.gov:4000/v1`, an OpenAI-compatible endpoint) has
repeatedly emitted its intent to call a tool as **plain text content**
instead of the OpenAI Chat Completions API's structured `tool_calls` field —
even on turns where this app offered it real, correctly-schema'd tool
definitions.

Three distinct hallucinated conventions have been observed in production, in
the same session, sometimes on the same turn:

1. `call:default_api:web_search{"query": "..."}` — a namespaced call, using
   `default_api` as the namespace.
2. `:default_api:search{"query": "..."}` — the same convention with the
   leading word dropped.
3. Degenerate repetition: `<tool_code></tool_code>` repeated ~250 times
   (~1000 streamed deltas), never resolving into a real answer or a real
   tool call, until this app's 30-second stream timeout cut it off.

`default_api` is the internal function-calling namespace Google's Gemini
models use natively. This strongly suggests `neraai-v1-raw` is Gemini-family
(or fine-tuned on Gemini function-calling data), and the serving stack in
front of it (vLLM or similar) either has no tool-call parser configured for
this model's chat template, or the "raw" mode this provider is registered
under (`NeraAiRaw`) bypasses whatever parser exists — so the model's real
intent to call a tool falls through to plain text content instead of landing
in `message.tool_calls`.

## Why this matters

This app's OpenAI-compatible client (`stackowl/providers/openai_provider.py`)
checks `choice.message.tool_calls` first, exactly as the API contract
promises. When that field is empty, the fallback path is a **best-effort
text parser** (`stackowl/providers/_react.py`) that recognizes this app's own
taught `ACTION:` format and a couple of native-call text shapes it has
learned to recognize after the fact. Every new hallucinated convention this
model happens to fall back to is, by construction, a shape this app has not
seen before — there is no way to enumerate them all from the application
side.

## What this app has done to compensate (not a fix to this gap)

- `providers/_react.py`: `looks_like_tool_call()` recognizes the `ACTION:`
  and native `name{...}`/`prefix:name{...}` shapes observed so far, and
  floors instead of delivering them raw.
- `pipeline/steps/execute.py`: a general, syntax-agnostic repetition guard
  now catches degenerate output (the same short unit repeated ~20+ times)
  regardless of what convention it's dressed in — this is the closest thing
  to a durable mitigation, since it doesn't depend on recognizing a specific
  syntax.

These are safety nets around a serving-side gap. They stop garbage from
reaching a user; they do not make the model's tool-calling work correctly.

## What would actually fix it

Someone with access to the `llm-gateway.dev.nera.gov` serving configuration
needs to either:

1. Configure a tool-call parser for `neraai-v1-raw`'s chat template (vLLM
   supports this via `--tool-call-parser` / a model-specific parser plugin),
   so the model's real tool-call intent lands in `message.tool_calls` like
   any other OpenAI-compatible tool-calling model, or
2. Confirm this "raw" endpoint is intentionally not meant to support
   tool-calling at all, in which case this app should stop offering it tool
   schemas entirely (a config/registry change on this side, not a code fix)
   rather than repeatedly hitting this failure mode.

Until one of those happens, expect this model to occasionally hallucinate
*new* text-based tool-call conventions this app has never seen before — the
repetition guard is the backstop for that unknown-unknown, not a cure.
