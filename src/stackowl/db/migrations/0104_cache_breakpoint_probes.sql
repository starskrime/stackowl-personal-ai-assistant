-- D01.2 — durable proof that an endpoint honours prompt-cache breakpoints.
--
-- WHY THIS TABLE EXISTS. A zero cache reading is AMBIGUOUS by construction. A
-- marker below the model's minimum, a genuinely cold cache, and a gateway that
-- strips the usage fields entirely all report exactly the same thing: zero. That
-- is D01.6's invariant I4, and it was not hypothetical — the baseline capture on
-- 2026-07-25 measured NeraAiRaw reporting no cache fields in ANY of the three
-- accepted shapes across 40 real calls.
--
-- Without somewhere to record a confirmed positive, that ambiguity is permanent:
-- every restart starts over, and "is caching working on this backend?" can only
-- ever be answered by reading logs. One row here answers it from data.
--
-- WHY ONLY POSITIVES ARE EVER WRITTEN. This is the load-bearing decision of the
-- whole item, and it was a contradiction Bakir resolved explicitly on 2026-07-27.
-- He first chose "trust cache_creation on the first response" AND "persist it to
-- the database". Those two interact badly: a field-stripping gateway guarantees a
-- zero on turn one, that zero would be persisted as "the marker is dead", and the
-- feature would disable itself FOREVER, across restarts, with no error anywhere.
-- Positives-only self-heals — the worst case is that we keep trying and waste
-- nothing.
--
-- WHY THE KEY IS (provider_name, model) AND NOT provider ALONE. The minimum
-- cacheable prefix is MODEL-dependent and NOT monotonic — 512 tokens on Opus 5
-- and Fable 5, 1024 on Opus 4.8 and the Sonnet 5/4.6/4.5 family, 2048 on Opus 4.7
-- and Haiku 3.5, 4096 on Opus 4.6/4.5 and Haiku 4.5. One model on a connection
-- confirming therefore says nothing about its sibling on the same connection, and
-- a provider-level row would launder that difference away.
--
-- WHAT THIS TABLE DELIBERATELY DOES NOT RECORD: which SPAN cached. Anthropic
-- returns ONE cache_creation_input_tokens for the whole request, not one per
-- marker, so with four markers a token count cannot be attributed to a span. The
-- only honest durable fact is "this endpoint honours cache_control at all", and
-- storing an inferred per-span figure would be a number a future reader trusts as
-- measured. (Bakir, 2026-07-27, choosing the endpoint-confirmed shape over a
-- per-span one for exactly this reason.)
--
-- The token columns are HIGH-WATER MARKS, not last-seen values: the question is
-- what this endpoint has been SEEN to do, and a later smaller reading means a
-- smaller prompt, not a degraded endpoint.
--
-- NO owner_id, DELIBERATELY. A provider endpoint's caching behaviour is a
-- property of the BACKEND, not of a principal — every owner talking to the same
-- model gets the same answer. Same reasoning as session_prompts (migration 0102):
-- a table inventing a scoping model its subject does not have is the more
-- confusing choice.

CREATE TABLE IF NOT EXISTS cache_breakpoint_probes (
    provider_name         TEXT    NOT NULL,
    model                 TEXT    NOT NULL,
    markers_placed        INTEGER NOT NULL,
    cache_creation_tokens INTEGER NOT NULL,
    cache_read_tokens     INTEGER NOT NULL,
    first_confirmed_at    TEXT    NOT NULL,
    last_confirmed_at     TEXT    NOT NULL,
    PRIMARY KEY (provider_name, model)
);
