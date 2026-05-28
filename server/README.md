# repoctx-reports server

Cloudflare Worker + D1 ingest endpoint for anonymous repoctx telemetry.

Receives NDJSON `POST /v1/events` from consenting `repoctx` clients (canary
default-on, stable opt-in only) and appends each row to a D1 SQLite table.

## What it stores

One row per event with:

- `channel` (`stable` | `canary`), `build_id`, `install_id` (client UUID)
- `op`, `success`, `error_type` (class only, never a message), `duration_ms`,
  `output_bytes`
- `repo_fingerprint` = `sha256(install_id || first_commit_sha)` — stable
  per (install, repo), not correlatable across users
- `stats_json` — free-form JSON for retrieval-tuning metrics

The Worker **rejects** any event whose top-level keys include path/query/code
identifiers (`path`, `query`, `code`, `task`, `error_message`, `remote_url`, …
full list in `src/index.ts`). The client is supposed to never send those; the
server enforces independently so a buggy client release can't leak.

## One-time setup

You need a Cloudflare account (free tier is enough) and Node.js installed.

```bash
cd server
npm install
npx wrangler login              # opens browser; one-time OAuth
npm run db:create               # creates the D1 database, prints database_id
```

Paste the printed `database_id` into `wrangler.toml` under `[[d1_databases]]`,
replacing `REPLACE_ME_AFTER_DB_CREATE`.

```bash
npm run db:migrate              # applies 0001_init.sql to the remote D1
npm run deploy                  # publishes the Worker; prints the URL
```

The URL looks like `https://repoctx-reports.<your-subdomain>.workers.dev`.
That's the value to bake into `repoctx.reporting.DEFAULT_ENDPOINT`.

## Smoke test

```bash
curl -X POST https://repoctx-reports.<subdomain>.workers.dev/v1/events \
  -H 'content-type: application/x-ndjson' \
  --data-binary @- <<'EOF'
{"schema_version":1,"event_type":"protocol_op","event_time":"2026-05-26T12:00:00Z","install_id":"smoke-test","channel":"canary","build_id":"smoke","op":"bundle","success":true,"duration_ms":42,"output_bytes":1024,"stats":{"files_considered":10,"files_selected":3}}
EOF
```

Expected: `{"accepted":1,"rejected":0,"reject_reasons":{}}` with HTTP 202.

Verify it landed:

```bash
npm run db:query -- "SELECT channel, op, duration_ms FROM events ORDER BY id DESC LIMIT 5"
```

## Local development

```bash
npm run db:migrate:local        # apply schema to the local D1 sim
npm run dev                     # wrangler dev on http://localhost:8787
```

## Operations

- `npm run tail` — live-stream Worker logs.
- `npm run db:query -- "<SQL>"` — ad-hoc queries against the production D1.
- Schema changes: add a new file to `migrations/` (e.g. `0002_add_x.sql`),
  then `npm run db:migrate`.

## Costs

Cloudflare free tier covers this comfortably:

- Workers: 100k requests/day free
- D1: 5 GB storage, 5M reads/day, 100k writes/day free

At expected repoctx volumes (a handful of canary users, tens to hundreds of
events per session) this stays at $0 indefinitely.
