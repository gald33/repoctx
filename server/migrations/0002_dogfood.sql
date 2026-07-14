-- Dogfood failure detail.
--
-- The default lane stores only an error *class* (error_type). Dogfood installs
-- (clients running REPOCTX_DOGFOOD=1) additionally upload the exception message
-- and traceback so the maintainer can actually debug a failure, not just count
-- it. These columns are NULL for every non-dogfood event.
--
-- `dogfood` mirrors the boolean the client sets and the Worker keys on to
-- decide whether to accept error_message/traceback; storing it lets queries
-- filter the two lanes apart (e.g. `WHERE dogfood = 1 AND success = 0`).

ALTER TABLE events ADD COLUMN dogfood INTEGER NOT NULL DEFAULT 0 CHECK (dogfood IN (0, 1));
ALTER TABLE events ADD COLUMN error_message TEXT;
ALTER TABLE events ADD COLUMN traceback TEXT;

-- Fast "recent dogfood failures" lookups — the query you actually run.
CREATE INDEX IF NOT EXISTS idx_events_dogfood_failures
  ON events(dogfood, success, event_time);
