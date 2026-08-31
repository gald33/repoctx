/**
 * repoctx reports ingest Worker.
 *
 * Accepts NDJSON POSTs from consenting repoctx clients and writes them to D1.
 * The privacy contract is enforced here: events with path/query/code-bearing
 * keys are rejected, channel must be exactly 'stable' or 'canary', and the
 * Worker stores nothing about the requester beyond what's in the payload.
 *
 * Endpoints:
 *   GET  /healthz     -> 200 'ok'
 *   POST /v1/events   -> ingests one or more NDJSON events
 */

export interface Env {
  DB: D1Database;
  MAX_BATCH_BYTES: string;
  MAX_EVENTS_PER_REQUEST: string;
}

interface EventV1 {
  schema_version: number;
  event_type: string;
  event_time: string;
  install_id: string;
  channel: "stable" | "canary";
  build_id: string;
  session_id?: string;
  op?: string;
  success?: boolean;
  error_type?: string | null;
  duration_ms?: number;
  output_bytes?: number;
  repo_fingerprint?: string;
  stats?: Record<string, unknown>;
  // Failure-detail fields. `dogfood: true` (set by installs running
  // REPOCTX_DOGFOOD=1) unlocks both; the canary channel unlocks
  // `error_message` alone. Otherwise they're rejected like any other
  // forbidden key.
  dogfood?: boolean;
  error_message?: string;
  traceback?: string;
}

// Forbidden keys the ingest accepts *only* on an event that declares
// `dogfood: true`. Kept in lockstep with the client's DOGFOOD_EXEMPT_KEYS.
const DOGFOOD_EXEMPT_KEYS = new Set(["error_message", "traceback"]);

// Additionally exempt on the canary channel, dogfood or not. Canary installs
// have opted into a deliberately less-stable lane, and an error *class* alone
// has proven too thin to act on: an IndexError burst across six ops was
// recorded with no way to tell what broke, because the failing installs
// weren't in dogfood mode. The message usually names the failing symbol.
//
// `traceback` is deliberately NOT here — frame lines carry absolute local
// paths, so it stays dogfood-only where the user opted in explicitly.
const CANARY_EXEMPT_KEYS = new Set(["error_message"]);

// Every op name the client is allowed to report. `op` is a closed vocabulary,
// not free text, so an allowlist is enforceable — and it has already caught a
// real leak: two rows reached production D1 with `op` set to a filesystem
// path ("/tmp/pp-fuzz/probe") from local fuzz testing. The schema forbids
// paths in this table; only type-checking `op` let one through.
//
// Adding a client-side op means adding it here and deploying the Worker,
// otherwise those events are rejected. That coupling is intentional: dropping
// telemetry for one release is recoverable, leaking a user's repo path is not.
const ALLOWED_OPS = new Set([
  "advisory_search",
  "authority",
  "bundle",
  "detect_changes",
  "get_task_context",
  "index",
  "install",
  "mark_used",
  "propose_authority",
  "refresh",
  "reporting",
  "risk_report",
  "scope",
  "semantic_search",
  "stats",
  "validate_plan",
]);

// Keys we refuse to accept anywhere in the event. Defense-in-depth — the
// client is supposed to never send these, but the server enforces the
// contract independently so a buggy client release can't leak.
const FORBIDDEN_KEYS = new Set([
  "path",
  "paths",
  "repo_path",
  "repo_root",
  "query",
  "query_text",
  "task",
  "task_text",
  "prompt",
  "code",
  "content",
  "remote_url",
  "remote",
  "git_remote",
  "hostname",
  "username",
  "user",
  "error_message",
  "error_msg",
  "stack_trace",
  "traceback",
]);

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/healthz") {
      return new Response("ok", { status: 200 });
    }

    if (url.pathname !== "/v1/events") {
      return new Response("Not found", { status: 404 });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { allow: "POST" },
      });
    }

    const maxBytes = parseInt(env.MAX_BATCH_BYTES ?? "262144", 10);
    const maxEvents = parseInt(env.MAX_EVENTS_PER_REQUEST ?? "1000", 10);

    const declaredLength = parseInt(
      request.headers.get("content-length") ?? "0",
      10,
    );
    if (declaredLength > maxBytes) {
      return jsonResponse({ error: "payload_too_large" }, 413);
    }

    const body = await request.text();
    if (body.length > maxBytes) {
      return jsonResponse({ error: "payload_too_large" }, 413);
    }

    const lines = body
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

    if (lines.length === 0) {
      return jsonResponse({ error: "empty_body" }, 400);
    }
    if (lines.length > maxEvents) {
      return jsonResponse({ error: "too_many_events" }, 413);
    }

    const receivedAt = new Date().toISOString();
    const rows: (string | number | null)[][] = [];
    let rejected = 0;
    const rejectReasons: Record<string, number> = {};

    for (const line of lines) {
      const parsed = tryParseEvent(line);
      if (!parsed.ok) {
        rejected++;
        rejectReasons[parsed.reason] = (rejectReasons[parsed.reason] ?? 0) + 1;
        continue;
      }
      const event = parsed.event;
      rows.push([
        receivedAt,
        event.event_time,
        event.schema_version,
        event.event_type,
        event.channel,
        event.build_id,
        event.install_id,
        event.session_id ?? null,
        event.op ?? null,
        event.success === undefined ? null : event.success ? 1 : 0,
        event.error_type ?? null,
        event.duration_ms ?? null,
        event.output_bytes ?? null,
        event.repo_fingerprint ?? null,
        event.stats ? JSON.stringify(event.stats) : null,
        event.dogfood ? 1 : 0,
        event.error_message ?? null,
        event.traceback ?? null,
      ]);
    }

    if (rows.length === 0) {
      return jsonResponse(
        { accepted: 0, rejected, reject_reasons: rejectReasons },
        400,
      );
    }

    const stmt = env.DB.prepare(`
      INSERT INTO events (
        received_at, event_time, schema_version, event_type,
        channel, build_id, install_id, session_id,
        op, success, error_type, duration_ms,
        output_bytes, repo_fingerprint, stats_json,
        dogfood, error_message, traceback
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    try {
      await env.DB.batch(rows.map((row) => stmt.bind(...row)));
    } catch (err) {
      return jsonResponse(
        { error: "db_write_failed", detail: String(err) },
        500,
      );
    }

    return jsonResponse(
      { accepted: rows.length, rejected, reject_reasons: rejectReasons },
      202,
    );
  },
};

type ParseResult =
  | { ok: true; event: EventV1 }
  | { ok: false; reason: string };

// Exported for tests. Cloudflare only ever uses the default export above, so
// the extra named export costs nothing at runtime.
export function tryParseEvent(line: string): ParseResult {
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    return { ok: false, reason: "invalid_json" };
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, reason: "not_object" };
  }
  const event = raw as Record<string, unknown>;

  if (event.dogfood !== undefined && typeof event.dogfood !== "boolean") {
    return { ok: false, reason: "bad_dogfood" };
  }
  const dogfood = event.dogfood === true;
  const canary = event.channel === "canary";

  // Forbidden-key check at the top level. We don't deep-scan stats — the
  // client is responsible for keeping the stats blob clean — but we DO
  // refuse any forbidden key on the event itself. Dogfood exempts
  // message+traceback; canary exempts the message alone. Everything else
  // stays forbidden on every channel.
  for (const key of Object.keys(event)) {
    if (!FORBIDDEN_KEYS.has(key)) continue;
    const exempt =
      (dogfood && DOGFOOD_EXEMPT_KEYS.has(key)) ||
      (canary && CANARY_EXEMPT_KEYS.has(key));
    if (!exempt) {
      return { ok: false, reason: `forbidden_key:${key}` };
    }
  }

  if (typeof event.schema_version !== "number") {
    return { ok: false, reason: "missing_schema_version" };
  }
  if (typeof event.event_type !== "string" || event.event_type.length === 0) {
    return { ok: false, reason: "missing_event_type" };
  }
  if (typeof event.event_time !== "string" || event.event_time.length === 0) {
    return { ok: false, reason: "missing_event_time" };
  }
  if (typeof event.install_id !== "string" || event.install_id.length === 0) {
    return { ok: false, reason: "missing_install_id" };
  }
  if (event.channel !== "stable" && event.channel !== "canary") {
    return { ok: false, reason: "bad_channel" };
  }
  if (typeof event.build_id !== "string") {
    return { ok: false, reason: "missing_build_id" };
  }

  // Optional fields — type-check if present.
  if (
    event.session_id !== undefined &&
    typeof event.session_id !== "string"
  ) {
    return { ok: false, reason: "bad_session_id" };
  }
  if (event.op !== undefined && event.op !== null) {
    if (typeof event.op !== "string") {
      return { ok: false, reason: "bad_op" };
    }
    // Closed vocabulary — anything else is a client bug, and the value may be
    // a path or other identifying string we must not persist.
    if (!ALLOWED_OPS.has(event.op)) {
      return { ok: false, reason: "unknown_op" };
    }
  }
  if (event.success !== undefined && typeof event.success !== "boolean") {
    return { ok: false, reason: "bad_success" };
  }
  if (
    event.error_type !== undefined &&
    event.error_type !== null &&
    typeof event.error_type !== "string"
  ) {
    return { ok: false, reason: "bad_error_type" };
  }
  if (
    event.duration_ms !== undefined &&
    (typeof event.duration_ms !== "number" || event.duration_ms < 0)
  ) {
    return { ok: false, reason: "bad_duration_ms" };
  }
  if (
    event.output_bytes !== undefined &&
    (typeof event.output_bytes !== "number" || event.output_bytes < 0)
  ) {
    return { ok: false, reason: "bad_output_bytes" };
  }
  if (
    event.repo_fingerprint !== undefined &&
    typeof event.repo_fingerprint !== "string"
  ) {
    return { ok: false, reason: "bad_repo_fingerprint" };
  }
  if (event.stats !== undefined) {
    if (
      !event.stats ||
      typeof event.stats !== "object" ||
      Array.isArray(event.stats)
    ) {
      return { ok: false, reason: "bad_stats" };
    }
  }
  if (
    event.error_message !== undefined &&
    typeof event.error_message !== "string"
  ) {
    return { ok: false, reason: "bad_error_message" };
  }
  if (event.traceback !== undefined && typeof event.traceback !== "string") {
    return { ok: false, reason: "bad_traceback" };
  }

  return { ok: true, event: event as unknown as EventV1 };
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
