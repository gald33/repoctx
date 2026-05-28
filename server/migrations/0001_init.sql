-- Initial schema for the repoctx-events D1 database.
--
-- One row per protocol-op invocation that a consenting repoctx client uploads.
-- All identifiers are anonymous:
--   - install_id is a client-generated UUID stored in ~/.repoctx/reporting.json
--   - repo_fingerprint is sha256(install_id || first_commit_sha), so it's
--     stable per (install, repo) but not correlatable across users
-- No paths, no query text, no code, no error messages (only error_type).
--
-- stats_json is a free-form JSON blob for retrieval-tuning metrics
-- (qualify_threshold_p50, files_considered/selected counts, etc.). It MUST
-- NOT contain repo paths, query text, or anything else that could identify
-- the user — the Worker rejects events whose top-level keys include those.

CREATE TABLE IF NOT EXISTS events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at     TEXT    NOT NULL,
  event_time      TEXT    NOT NULL,
  schema_version  INTEGER NOT NULL,
  event_type      TEXT    NOT NULL,
  channel         TEXT    NOT NULL CHECK (channel IN ('stable', 'canary')),
  build_id        TEXT    NOT NULL,
  install_id      TEXT    NOT NULL,
  session_id      TEXT,
  op              TEXT,
  success         INTEGER CHECK (success IN (0, 1)),
  error_type      TEXT,
  duration_ms     INTEGER,
  output_bytes    INTEGER,
  repo_fingerprint TEXT,
  stats_json      TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_event_time   ON events(event_time);
CREATE INDEX IF NOT EXISTS idx_events_channel_op   ON events(channel, op);
CREATE INDEX IF NOT EXISTS idx_events_install_id   ON events(install_id);
CREATE INDEX IF NOT EXISTS idx_events_channel_time ON events(channel, event_time);
