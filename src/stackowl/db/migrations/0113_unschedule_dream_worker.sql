-- Migration 0113 — stop waking the dream worker.
--
-- All five of its phases were fact work — mining, promotion, contradiction
-- detection, pruning and fact-to-graph sync — and every one went with the
-- extraction pipeline in D08.1. The job was still scheduled `every 30m`, so it
-- would wake 48 times a day, find an empty store, and record a SUCCESS.
--
-- That is worse than no job at all. A loop whose runs all succeed while doing
-- nothing is indistinguishable, in the brief and in job_results, from a loop
-- that is working — which is the exact failure ADR-19 was written to kill.
--
-- THE HANDLER IS NOT DELETED, and this is deliberate. Bakir, 2026-08-10:
-- "DreamWorker should be in platform. because i am not going to create a next
-- chatbot which does no interaction. thats why i am thinking to build jarvis
-- which will dream and rethink about his life, his abilities, his growing,
-- learning, improving and etc things." That is item N01, and this handler —
-- with its checkpoint/resume machinery, already built for long multi-phase
-- background work — is the seat it will occupy.
--
-- Disabled rather than deleted so the row's history (last_run_at, failure_count)
-- survives, and so re-arming it is one UPDATE when N01 has its first real phase.

UPDATE jobs
   SET enabled = 0,
       last_error = 'unscheduled by migration 0113 — all phases were fact work '
                    || '(D08.1); the handler awaits N01 Dreaming'
 WHERE handler_name = 'dream_worker';
