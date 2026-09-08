-- Migration 0140 the DENOMINATOR, stored beside the numerator it belongs to.
--
-- cost_records has persisted input_tokens since it was created and the context
-- window NOWHERE. The window lives in providers/model_window.py's in-process
-- cache, which is EMPTY AT REST, so a closing check running in a fresh shell
-- cannot resolve it at all. The ratio was therefore computable only at the
-- moment of the call, and the one line that fires at that moment did not record
-- it -- so D03.2's check had to hardcode an assumed 262144, and the 2026-08-30
-- measurement that FALSIFIED D03.2's closure had to assume the same number.
--
-- MEASURED 2026-09-08 on the live database: 131,644 rows, 7 over 50 percent of
-- an assumed 262,144 window, 390 over 25 percent, peak 162,912. Every one of
-- those percentages is an inference about a number nobody wrote down.
--
-- Nullable on purpose. Existing rows have no honest value to backfill -- the
-- window in force when they were written is not recoverable -- and NULL says
-- that, where a DEFAULT would manufacture the very assumption this removes.
-- NOTE no semicolons inside comments per the runner split gotcha.

ALTER TABLE cost_records ADD COLUMN context_window INTEGER;
