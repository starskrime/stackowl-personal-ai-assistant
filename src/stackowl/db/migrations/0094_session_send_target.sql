-- D01.7 slice 3a.2 — the lane stores its own send target.
--
-- WHY: a lane is about to become a COMPOSITE key ("owl:Brain:telegram:dm:12345").
-- Delivery used to recover the recipient by int()-ing the lane, which worked only
-- because the lane WAS the Telegram chat id. That stops being true here, and it
-- was never true on Slack, where the lane is a hash and the send target is a
-- channel id. Storing the native target is honest for every channel; parsing the
-- key would couple every delivery path to the key's exact shape, including its
-- optional thread and participant components.
--
-- NULL is a real answer, not a missing one: CLI has no per-lane destination, and
-- the deliverer's contract is to fall back loudly rather than fabricate a
-- recipient (a fabricated chat id is precisely the cross-delivery bug).

ALTER TABLE sessions ADD COLUMN chat_id TEXT;
