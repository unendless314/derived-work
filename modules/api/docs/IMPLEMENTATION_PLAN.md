# API Implementation Plan

**Document version:** v1.5
**Updated:** 2026-09-02
**Status:** Active (approved for implementation 2026-09-02)
**v1.1 changes (external review, 2026-09-02):** `requirements.txt` explicitly pins PyYAML; loopback binding is a code constant with no configuration surface, with a test; Phase 5 gains an ops verification step for the nginx `429` JSON path.
**v1.2 changes (follow-up review, 2026-09-02):** Phase 5 verification now covers nginx-generated `502` and `504` JSON envelopes and `Cache-Control: no-store` on every edge-generated response.
**v1.3 changes (follow-up review, 2026-09-02):** FastAPI's framework-generated `404` and `405` responses are required to use the common error envelope, with contract tests.
**v1.4 changes (follow-up review, 2026-09-02):** `RequestValidationError` → `400` mapping made explicit — the framework-default `422` is outside the contract and must never leak; contract test added.
**v1.5 changes (follow-up review, 2026-09-02):** cursor identity now includes normalized query filters, and the nginx request-body `413` edge path is specified and manually verified.

---

## 1. Scope

Deliver the v1 contract in `API_CONTRACT.md` over the data dependencies in `DATA_DEPENDENCIES.md`, operated per `EXECUTION_POLICY.md`. Non-goals for v1 (proposal §3.3): no archives/historical queries, no `category`, no `bullets`/`disclosure_note`/`author_metadata` fields, no second-endpoint additions, no metrics endpoint.

---

## 2. Module Layout

```text
modules/api/
  docs/                          # this doc set
  config/
    api_settings.yaml            # service/export/freshness/auth settings (no host key — see §3)
  requirements.txt               # fastapi, uvicorn, PyYAML pins
  src/
    __init__.py
    cli.py                       # validate / serve commands
    config.py                    # YAML loading + env precedence + strict validation
    export_pointer.py            # current.json validation + generation resolution
    adapter.py                   # index read, event-time filter, paginate, items join, coverage assembly (pure, no FastAPI)
    cursor.py                    # opaque cursor codec: generation + last (source_published_at, slug)
    app.py                       # FastAPI app, auth dependency, error mapping, Cache-Control
  tests/
    __init__.py
    support.py
    fixtures/publish_export/     # mirrors modules/site/tests/fixtures/publish_export/ pattern
    test_pointer.py
    test_adapter.py
    test_cursor.py
    test_api_contract.py
    test_auth.py
```

Conventions followed: `python -m modules.api.src.cli <command>` (publish/translate precedent), `test_<unit>.py` naming (AGENTS.md), fixtures beside tests (site precedent).

---

## 3. Phases

### Phase 1 — Scaffold, config, pointer resolution

- `requirements.txt` with explicit pins: `fastapi`, `uvicorn`, and `PyYAML>=6,<7` — `config.py` parses YAML, and PyYAML is not guaranteed to arrive as a transitive dependency of the web stack, so the module must install standalone in a clean environment.
- `config/api_settings.yaml` + `config.py` with the precedence rules of `EXECUTION_POLICY.md` §5 and fail-fast validation (including the token-env-var presence check). **The bind address is a fixed `127.0.0.1` constant with no configuration surface** — no YAML key, no CLI flag, no env var — and config validation rejects unknown keys so a stray `host:` entry fails loudly (`EXECUTION_POLICY.md` §3).
- `export_pointer.py`: pointer validation per `DATA_DEPENDENCIES.md` §4 — generation-id regex before any path join, calendar-valid timestamp round-trip, fingerprint format, non-empty `languages`, generation directory existence. Parity target: `modules/site/src/utils/export_root.js` and `modules/publish/src/generation_store.py`.
- `cli.py validate`.
- Fixture base: `tests/fixtures/publish_export/` with a minimal two-generation tree (mirroring the site fixture layout).

### Phase 2 — Adapter (pure logic, no web framework)

- `adapter.py`: load `<lang>/index.json`; filter by `[event_from, event_to]` on `source_published_at` **date** semantics; total ordering (`source_published_at` desc, `slug` desc); page slice; per-slug join into `items/` for `source_item_id`/`downstream_action`; coverage assembly (`window_from`/`window_to` from the in-index event-time bounds, `basis: "index"`, pointer fields, `request_exceeds_window_to`, `data_may_be_stale` per `EXECUTION_POLICY.md` §6, `items_without_event_time`).
- `cursor.py`: opaque token encoding `generation` + normalized `event_from`, `event_to`, and `language` + last `(source_published_at, slug)`; decode validates shape, rejects foreign generations with `cursor_expired`, and rejects changed query filters with `cursor_query_mismatch`. `limit` remains request-scoped, not cursor-bound.
- Mid-read sweep handling: the retry scope from `EXECUTION_POLICY.md` §8 lives here, so the web layer stays thin.

### Phase 3 — HTTP layer

- `app.py`: FastAPI app; `GET /v1/articles`; query-param validation producing the contract's `400` bodies (including the supported-language list), with the framework's `RequestValidationError` handler overridden so malformed parameters map to that `400` envelope — FastAPI's default `422` is not part of the contract and must never leak; Bearer auth dependency (`hmac.compare_digest`, `401` + `WWW-Authenticate`); application exception handlers that make every application-generated error — including framework `404` and `405` responses — use the shared `{"error": {"code", "message", ...}}` shape (`API_CONTRACT.md` §§1, 4); `Cache-Control: no-store`; `Retry-After: 30` on `503`; error code `cursor_expired` on generation mismatch.
- `cli.py serve` wiring uvicorn to the fixed loopback constant and the configured port.
- Manual smoke pass against the live export (read-only) before Phase 4.

### Phase 4 — Contract-derived test suite

- Implement every scenario in §4 below; run the full module suite plus the repo's existing suites (`pytest modules/`) to confirm no cross-module regression.

### Phase 5 — Ops packaging and cross-module docs

- systemd unit requirements and the nginx reference block (with `limit_req_zone`, `limit_req_status 429`, and the JSON `error_page` handler) are specified in `EXECUTION_POLICY.md` §§2–3; deployment on the VPS is an owner operation.
- **Ops verification after nginx reload:** an oversized-body request returns JSON `413` with `Cache-Control: no-store`; a request burst returns JSON `429` with `Retry-After` and `Cache-Control: no-store` (not HTML `503`), and normal requests pass through to the service. In a controlled maintenance window, stop the API unit briefly to verify the TLS edge returns JSON `502` with `Retry-After` and `Cache-Control: no-store`, then restore the unit; validate the `504` handler with the deployment's nginx configuration test procedure. These paths live at the edge and cannot be covered by pytest; they are manual deployment checklist items.
- Top-level documentation, landed in the same change (proposal §10): `docs/MODULE_BOUNDARIES.md` (new §3.10 from proposal §4), `docs/SYSTEM_OVERVIEW.md` (module sequence/diagram), `docs/DATA_LIFECYCLE.md` (downstream consumer entry), `docs/IMPLEMENTATION_ROADMAP.md` (work item), `AGENTS.md` (module list).
- Verify README CLI examples against the delivered commands.

---

## 4. Required Test Scenarios (contract-derived checklist)

Pointer and language authority:

- valid pointer resolves; generation id failing the regex (including path-traversal-shaped strings) → `503`, never a path join
- calendar-impossible timestamp (`2026-02-30T00:00:00Z`) → invalid pointer → `503`
- missing `current.json` (bootstrap state) → `503` with `Retry-After`
- **stale residue at export root:** a valid pointer plus unlisted pre-refactor directories (the real 2026-08-05 residue shape, `DATA_DEPENDENCIES.md` §3) → requests for an unlisted `language` return `400` with the supported list; residue is never served

Configuration and binding:

- a YAML `host:` key (or any unknown key) is a fail-fast validation error
- the service binds the fixed loopback constant: no YAML key, CLI flag, or environment variable can produce a non-loopback bind
- token env var unset → `validate` fails, `serve` refuses to start

Query semantics:

- default range = today (UTC); inclusive bounds; `event_from > event_to` → `400`; malformed date → `400`
- `limit` default 100, max 500, out-of-range → `400`
- framework validation failures never leak FastAPI's default `422`: every malformed-parameter response is the contract's `400` envelope
- ordering: `source_published_at` desc, `slug` desc tie-break — construct entries with equal timestamps to pin the tie-break
- an old item approved recently is excluded from "this week"; a recent item approved late is included (contract §3 consequences)
- range older than `window_from` returns in-window matches, not an error

Pagination:

- cursor round-trip across pages yields gap-free, duplicate-free concatenation; `total_count` stable across pages; `next_cursor` null on final page; changing only `limit` between pages remains valid
- generation switch between pages → `400` with code `cursor_expired`; language or normalized date-range change between pages → `400` with code `cursor_query_mismatch`; tampered cursor → `400`

Coverage and freshness:

- `request_exceeds_window_to` true only when the range extends beyond the window
- `data_may_be_stale` flips exactly at the `freshness_sla_hours` boundary (freeze time in the fixture)
- `items_without_event_time` counts in-window entries with missing/unparseable `source_published_at`, and such entries are excluded from results

Consistency and failure model:

- generation swept mid-read (delete the generation directory between resolution and join, via monkeypatch) → transparent single retry against the new pointer; second sweep → `503` + `Retry-After`
- malformed `index.json` inside a resolved generation → `500` (no retry)
- every response carries `Cache-Control: no-store`
- all application error bodies match the `{"error": {"code", "message", ...}}` shape (`API_CONTRACT.md` §1), including malformed-query `400`, authentication `401`, unknown-route `404`, and method `405` responses; the nginx `413`, `429`, `502`, and `504` edge paths are verified manually in Phase 5, not here

Authentication:

- missing token, wrong token → `401` + `WWW-Authenticate: Bearer`; correct token passes
