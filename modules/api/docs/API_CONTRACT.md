# `api` Module — v1 Contract

**Status:** v1.15 — implemented 2026-09-03 (approved for implementation, owner decision 2026-09-02). Builds on the completed publish generation-pointer refactor (Phase B1 landed 2026-08-18, Phase B2 2026-08-22; basis: `known_issues/resolved/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` v7).
**Updated:** 2026-09-04

**Scope:** v1 serves the deep-reader agent only. It is deliberately not designed as a general-purpose content API; generalization decisions are deferred until a second consumer exists.

**Product constraint (owner decision, 2026-08-17):** the site is a breaking-news aggregator with strong timeliness. Deep-reader queries are almost always about the recent past ("today", "the day before", "this week so far"). Content older than roughly a month is permanently archived and has no query demand. v1 is scoped to the recent window only — this is a product definition, not a limitation.

---

## 1. Conventions

- All endpoints are prefixed with `/v1/`.
- All application responses, plus the documented TLS-edge errors (`413`, `429`, `502`, and `504`), are JSON, UTF-8. Low-level HTTP parsing and connection failures that occur before nginx selects `/v1/` are outside this API contract.
- Error responses carry a JSON body of the form `{"error": {"code": "<machine-readable code>", "message": "<human-readable summary>"}}`, with optional additional fields where noted (e.g. `supported_languages` on an unsupported-language `400`).
- The service is read-only: only `GET` is supported in v1.
- Dates use `YYYY-MM-DD`; timestamps use ISO 8601 UTC.
- "Today" means the current UTC date.
- Response field names follow the publish-export naming verbatim (`display_title`, `summary_short`, `canonical_url`, ...). The adapter is a thin pass-through; it must not rename or re-derive semantics.
- All `/v1/` endpoints require Bearer authentication; see `EXECUTION_POLICY.md` §4. (The contract data served is public site content — authentication is an anti-abuse control, not a confidentiality boundary.)

## 2. What "published" Means

v1 has **no `status` parameter**. An item's presence in the current export generation already implies a conjunction of upstream conditions: curation-approved, translation completed for the language, fingerprint match, language coverage satisfied, and not withdrawn. Re-exposing a single `status=approved` filter would misrepresent that semantics.

If consumers ever need to query non-published states, that requires a formal extension of the publish export contract — out of scope for v1.

## 3. Time Semantics: Event Time, Not Processing Time

v1 filters and sorts by **`source_published_at`** — the time the external source published the item (from feed metadata). This is the only timestamp in the export that refers to the external real world, and it is what a reader means by "today's news".

The other timestamps are internal processing times and are **not** used for filtering:

- `approved_at` — when curation approved the item
- `published_at` — when the item entered the export
- `author_metadata.upstream_updated_at` — when the upstream module last touched the record (also internal; typically equals `approved_at`)

Consequences, both intended:

- An old article approved today (e.g. a 2010 source item approved this week) does **not** appear in "this week" queries — it is not this week's news.
- A recent event approved late (event today, approved tomorrow) **is** correctly captured by event-time queries.

## 4. Endpoint: List Articles

```
GET /v1/articles?event_from=YYYY-MM-DD&event_to=YYYY-MM-DD&language=<code>&limit=100&cursor=...
```

### Query parameters

| Parameter | Required | Default | Notes |
|:---|:---:|:---|:---|
| `event_from` | no | today (UTC) | inclusive lower bound on `source_published_at` date |
| `event_to` | no | today (UTC) | inclusive upper bound on `source_published_at` date |
| `language` | no | `zh` | must be listed in `current.json` (`DATA_DEPENDENCIES.md` §3); unsupported codes return `400` with the supported list |
| `limit` | no | `100` | max `500` |
| `cursor` | no | — | opaque pagination token from a previous response for the same normalized `event_from`, `event_to`, `language`, and `include` set |
| `include` | no | — | comma-separated projection list; the v1 allowlist is exactly `bullets`. Unknown values, empty entries, duplicated entries, or a repeated `include` query key return `400` |

### Response

```json
{
  "range": { "event_from": "2026-08-16", "event_to": "2026-08-17" },
  "language": "zh",
  "coverage": {
    "window_from": "2026-07-02T15:24:00Z",
    "window_to": "2026-08-05T10:10:58Z",
    "basis": "index",
    "generation": "2026-08-05T15-23-51Z",
    "export_completed_at": "2026-08-05T15:23:51Z",
    "last_successful_run_at": "2026-08-17T08:05:11Z",
    "request_exceeds_window_to": true,
    "data_may_be_stale": false,
    "items_without_event_time": 0
  },
  "total_count": 0,
  "returned_count": 0,
  "next_cursor": null,
  "articles": []
}
```

### Field definitions

| Field | Source | Notes |
|:---|:---|:---|
| `source_item_id` | full item record (`items/`) | stable numeric identifier; obtained by joining on `slug` |
| `slug` | index entry | publish-frozen; also the item filename key and join key |
| `display_title` | index entry / item record | translated, per `language` |
| `summary_short` | index entry / item record | translated, per `language` |
| `canonical_url` | index entry / item record | the original source URL the deep-reader fetches |
| `source_published_at` | index entry | **event time** — basis for filtering and primary sort |
| `approved_at` | index entry | internal; reference only |
| `published_at` | index entry | internal; reference only |
| `downstream_action` | full item record (`items/`) | e.g. `publish_summary`, `publish_link`; tells the agent whether richer readable content exists or only the link |
| `bullets` | full item record (`items/`) | opt-in — present only when `include=bullets` is passed, then present on **every** article in the page: the fixed three-key object `{key_claim, evidence_level, objective_impact}` on `publish_summary` items, `null` on `publish_link` items. Thin pass-through of publish semantics — never omitted, never re-derived. A malformed `bullets` shape is malformed generation data → `500`, never silently dropped |

Deliberately excluded in v1: `category` (not present in the export; adding it requires a formal publish export contract extension), `disclosure_note`, `author_metadata` (not needed by the deep-reader). `bullets` is no longer excluded outright — it ships as the opt-in projection above, default off, so the baseline projection and its token cost are unchanged.

### Ordering and pagination

- Sort: `source_published_at` descending; tie-breaker: `slug` **ascending**, matching the publish index contract (`modules/publish/docs/DATA_CONTRACT.md`: `source_published_at DESC, slug ASC`) — the filtered index is therefore already in response order, and the whole system shares one ordering convention. Index entries carry no `source_item_id`, so `slug` is the stable tie-breaker at this layer; the cursor's comparison direction must match this external order. (Pre-v1.15 drafts specified `slug` descending — corrected before implementation.) This makes ordering total and stable.
- `total_count` = number of items matching the filter within the coverage window (all pages); `returned_count` = items in this response.
- `cursor` is an opaque token encoding **the generation**, normalized `event_from`, `event_to`, `language`, and `include` set, plus the last `(source_published_at, slug)` of the previous page. `next_cursor` is `null` on the final page. `limit` is deliberately not cursor-bound — the only parameter with this exception — so a caller may safely choose a different page size for its next request.
- **Cursors are integrity-protected.** The token carries an HMAC-SHA256 signature over its payload, keyed by a server-only random secret generated at service startup — never derived from the Bearer token, which the caller holds and could otherwise use to sign cursors itself. A tampered or re-encoded payload (e.g. an altered position slug) fails with `400` and error code `invalid_cursor` — never a silent position shift that would skip or duplicate records. A service restart invalidates outstanding cursors; the client restarts from page 1 (acceptable for short-lived pagination state — the pipeline cadence expires cursors on content changes anyway).
- **Cursors are generation- and query-bound.** If the pointer's generation differs from the cursor's generation (a new generation was published between pages), the request fails with `400` and error code `cursor_expired`; the client must restart from the first page. If the cursor's normalized date range, language, or `include` set differs from the request, it fails with `400` and error code `cursor_query_mismatch`; the client must restart from the first page. The API must never silently mix pages from two generations or query filters — either would produce gaps, duplicates, and inconsistent `total_count`. `include` changes only the projection, never membership or ordering, but a mid-read projection switch would silently weaken the single-consistent-query guarantee, so it is bound like the filters.
- **Caller guidance:** for multi-page reads, pass `event_from` and `event_to` explicitly rather than relying on defaults. The cursor binds the *normalized* filters, and the defaults resolve "today" (UTC) at request time — a paginated read that crosses a UTC midnight with default parameters therefore fails with `cursor_query_mismatch`. Restarting from page 1 recovers cleanly, but explicit dates avoid the detour.

### Coverage and freshness semantics

- `coverage.window_from` / `coverage.window_to` describe the index window actually searched (`DATA_DEPENDENCIES.md` §2).
- `coverage.generation` / `coverage.export_completed_at` identify the export generation being served; `coverage.last_successful_run_at` is the pipeline health signal — all three come from `current.json` (§5).
- **Historical gap:** a query range (partially) older than `window_from` is not an error — the API returns matches inside the window. `coverage` tells the agent that older content exists but is out of scope.
- **Content coverage vs. pipeline health are separate signals:**
  - `request_exceeds_window_to` is `true` when the request range extends beyond `window_to` (no items in the window for the requested dates). With a healthy pipeline this can be a legitimate editorial fact: nothing new happened.
  - `data_may_be_stale` is `true` when `last_successful_run_at` is older than the configured freshness threshold (`freshness_sla_hours`, default `6`; see `EXECUTION_POLICY.md` §6). This means the **pipeline itself** may not have run — independent of whether content changed. (A successful run refreshes `last_successful_run_at` even when no content changed; see the refactor plan v7.)
- **Agent guidance (normative):** when `data_may_be_stale` is `true`, the agent must report "the site's pipeline has not run recently" — never "there was no news". When only `request_exceeds_window_to` is `true` and the pipeline is fresh, the agent may report that no new items appeared in the window.
- `coverage.items_without_event_time` counts in-window items skipped because `source_published_at` was missing or unparseable (feed metadata quality varies by source).

### Errors

| Status | Condition |
|:---|:---|
| `400` | malformed date; `event_from` > `event_to`; unsupported `language` (error body lists supported codes from `current.json`); `limit` out of range; invalid `include` (unknown value, empty entry, duplicated entry, or a repeated `include` query key); invalid `cursor` (undecodable, shape-invalid, or tampered/re-encoded — signature mismatch); `cursor_expired` (cursor generation ≠ current pointer generation — restart from page 1); `cursor_query_mismatch` (cursor language, normalized date range, or `include` set ≠ request — restart from page 1) |
| `401` | missing or invalid Bearer token; response carries `WWW-Authenticate: Bearer` (see `EXECUTION_POLICY.md` §4) |
| `404` | no `/v1/` route matches the request; application-generated JSON body with error code `not_found` |
| `405` | the request uses a method other than `GET` for an existing `/v1/` route; application-generated JSON body with error code `method_not_allowed` plus an `Allow: GET` header |
| `413` | request body exceeds the nginx `/v1/` body-size limit; JSON body with error code `payload_too_large` — emitted at the TLS edge (`EXECUTION_POLICY.md` §3) |
| `429` | request rate exceeded at the TLS edge (nginx `limit_req`); JSON body with error code `rate_limited` plus a `Retry-After` header — emitted by the edge, never by the service itself in v1. Kept distinct from `503` so the caller can tell throttling apart from export/pipeline unavailability (`EXECUTION_POLICY.md` §3) |
| `500` | unreadable or malformed export data within a resolved generation |
| `502` | nginx cannot reach the API process (for example, while systemd restarts it); JSON body with error code `api_unavailable` plus a `Retry-After` header — emitted at the TLS edge, distinct from the application's export-specific `503` (`EXECUTION_POLICY.md` §3) |
| `503` | export not serveable: `current.json` missing/invalid, or the generation still unresolvable after one full-flow retry (see §5); response includes `Retry-After` |
| `504` | nginx timed out while waiting for the API process; JSON body with error code `api_timeout` plus a `Retry-After` header — emitted at the TLS edge (`EXECUTION_POLICY.md` §3) |

## 5. Read Consistency: Generation Pointer

Phase B1 of the publish refactor has landed (2026-08-18): each content-changing export run produces an **immutable generation directory** and atomically switches `current.json` (single-file `os.replace`, same volume) only after the generation is complete; successful no-change runs atomically refresh `current.json.last_successful_run_at` without a new generation. A forced `rebuild` also switches the generation even when the content fingerprint is unchanged, so any generation difference — not only a fingerprint change — expires cursors (§4).

Read protocol per request — the **entire read flow is wrapped in a single retry scope**, because retention can sweep the resolved generation at any point, not just during resolution:

1. Read `current.json`. Missing/invalid → `503` with `Retry-After` (the export has never completed — bootstrap has not yet established a pointer).
2. Resolve `generations/<generation>/`, read `index.json`, perform the `items/` joins, and assemble the response.
3. If **any** step of 2 fails because the generation directory (or a file within it) has vanished — including mid-join — re-read `current.json` and **re-run the whole flow once** with the new pointer. If it still fails → `503` with `Retry-After`.

Pointer payload validation (field shapes, generation id format, no-fallback rules) is defined in `DATA_DEPENDENCIES.md` §4 and applies before any path is joined.

Two further consistency rules:

- Because generation directories are immutable after publication, **no mid-read content revalidation exists**. The retry covers exactly one failure mode: the resolved generation being swept by retention at any point during the read. Within a live generation, drift is impossible by construction.
- Pagination does **not** follow a generation, query-filter, or projection switch: cursors pin the generation, normalized query filters, and normalized `include` set they were issued from (§4), so a multi-page read never silently mixes generations, filters, or projections. A client that hits `cursor_expired` or `cursor_query_mismatch` restarts from page 1 and gets a consistent series.

The consistency burden lives once in the writer (publish), not in every reader.

## 6. Versioning Policy

- Breaking changes (field removal, type change, semantics change) require a new prefix (`/v2/`); `/v1/` must keep working while any consumer depends on it.
- Additive changes (new optional fields, new optional parameters) may ship within `/v1/`. New `include` projection values (e.g. a future `disclosure_note`) are additive and follow this rule; the default projection (no `include`) must never gain fields silently. For `bullets`, the name, type, `null` semantics, and three-key shape are breaking-change territory (`/v2/`).
