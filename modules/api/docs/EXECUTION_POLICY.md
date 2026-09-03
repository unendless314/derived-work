# API Execution Policy

**Document version:** v1.4
**Updated:** 2026-09-04
**Status:** Active (implemented 2026-09-03; approved for implementation 2026-09-02)

---

## 1. Purpose

This document defines how the `api` service is operated: process form, network exposure, authentication, configuration, freshness computation, caching, retry internals, failure model, and observability. The consumer-visible wire semantics live in `API_CONTRACT.md`; this document owns the operational side.

---

## 2. Service Form

- The service is launched as a CLI command, matching repo module conventions:
  - `python -m modules.api.src.cli validate` — loads configuration, validates it, checks that the export root exists and is readable/traversable by the service account (an unreadable root would otherwise pass startup and surface later as a misleading per-request `503`/`500`), and exits non-zero on any failure. Does **not** require `current.json` to exist (bootstrap is a runtime `503` state, not a config error).
  - `python -m modules.api.src.cli serve` — starts uvicorn with the FastAPI app, bound to the fixed loopback address (§3) on the configured port.
- On the production VPS the process runs under a systemd unit (`exopolitics-api.service`, `Restart=on-failure`), alongside the existing `exopolitics-pipeline.timer`. The unit file itself is ops material, kept out of the repo; its requirements are: working directory at the repo root, the token environment variable provided via the unit's `EnvironmentFile` (not committed), and journald logging.
- Single process. No workers fan-out, no background threads, no scheduled tasks inside the service.

---

## 3. Network Exposure

- The service binds `127.0.0.1` only, and this is **enforced in code, not by configuration**: the bind address is a fixed constant, `config/api_settings.yaml` carries no `host` key, no CLI flag or environment variable overrides it, and config validation rejects unknown keys so a stray `host:` entry cannot silently reappear. A misconfiguration therefore cannot expose the port on a public interface and bypass nginx's TLS and rate limiting. Only the listen **port** is configurable.
- The existing nginx + Let's Encrypt TLS site (`https://exopolitics.tw`) proxies the API. Reference configuration:

```nginx
# http context:
limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;

# server context (existing exopolitics site):
location /v1/ {
    # v1 is GET-only. Reject request bodies above this defensive limit at
    # the edge rather than accepting arbitrary uploads for a read-only API.
    client_max_body_size 1k;
    limit_req zone=api burst=10 nodelay;
    limit_req_status 429;
    error_page 429 = @api_rate_limited;
    error_page 413 = @api_payload_too_large;
    error_page 502 = @api_bad_gateway;
    error_page 504 = @api_gateway_timeout;
    # The upstream port must match service.port in config/api_settings.yaml
    # (§5) — this block assumes the default; a mismatch makes every proxied
    # request a 502.
    proxy_pass http://127.0.0.1:8010;
    proxy_set_header Host $host;
}

location @api_rate_limited {
    default_type application/json;
    add_header Retry-After 60 always;
    add_header Cache-Control "no-store" always;
    return 429 '{"error":{"code":"rate_limited","message":"Rate limit exceeded; retry after the indicated delay."}}';
}

location @api_payload_too_large {
    default_type application/json;
    add_header Cache-Control "no-store" always;
    return 413 '{"error":{"code":"payload_too_large","message":"Request body exceeds the API limit."}}';
}

location @api_bad_gateway {
    default_type application/json;
    add_header Retry-After 30 always;
    add_header Cache-Control "no-store" always;
    return 502 '{"error":{"code":"api_unavailable","message":"API process is temporarily unavailable; retry after the indicated delay."}}';
}

location @api_gateway_timeout {
    default_type application/json;
    add_header Retry-After 30 always;
    add_header Cache-Control "no-store" always;
    return 504 '{"error":{"code":"api_timeout","message":"API process did not respond in time; retry after the indicated delay."}}';
}
```

- The `proxy_pass` upstream port above must match `service.port` in `config/api_settings.yaml` (§5): the port is the one configurable network value, and the reference block assumes the default `8010`. A deployment that configures a different port must update the upstream in the same change, or nginx proxies to a closed port and every `/v1/` request returns `502`.
- Rate limiting lives at the nginx layer, keyed on `$binary_remote_addr`; 30 r/m with a small burst fits the single daily batch caller and blocks basic abuse. The service itself implements no rate limiting in v1.
- **Throttling semantics:** the edge returns `429` with a JSON body and `Retry-After`, never nginx's default HTML `503`. `503` stays reserved for "publish export not serveable", so the caller can distinguish throttling from pipeline failure (`API_CONTRACT.md` §4).
- **Request-body semantics:** v1 exposes only `GET`, so clients must not send a request body. nginx limits `/v1/` request bodies to `1k`; an oversized body receives edge-generated JSON `413` with code `payload_too_large`. It is a request error, not a transient condition, so it carries no `Retry-After`.
- **Upstream-process failure semantics:** nginx returns `502` with `api_unavailable` when it cannot reach the loopback API process, and `504` with `api_timeout` when that process times out. These edge failures remain distinct from the application's export-specific `503`. The default `proxy_intercept_errors` behavior must remain unchanged, so JSON `503` responses from the API itself pass through without being rewritten.
- nginx logging must not record the `Authorization` header (default combined formats do not; any custom format must keep it that way).

---

## 4. Authentication

- All `/v1/` endpoints require `Authorization: Bearer <token>`; missing or invalid credentials return `401` with `WWW-Authenticate: Bearer` (see `API_CONTRACT.md` §4 error table).
- v1 issues **one** long-lived token to the single known caller. No per-client identities, OAuth, or rotation machinery until a second caller exists (proposal §8.2).
- The token is read from the environment variable named by `token_env_var` (default `EXOPOLITICS_API_TOKEN`). If the variable is unset or empty at startup, `validate` fails and `serve` refuses to start — running without authentication is not a supported mode.
- Comparison uses a constant-time primitive (`hmac.compare_digest`). The token is never written to logs, error bodies, config files, or the repository; the caller stores it in an env file or secret store on its own side.
- Pagination cursors are HMAC-signed with a server-only random secret generated at each service start (`cursor.py: generate_cursor_secret`): never persisted, never derived from the Bearer token (the caller holds it and could otherwise sign cursors itself), and never leaves the process. A service restart invalidates outstanding cursors; clients restart pagination from page 1 (`API_CONTRACT.md` §4).
- Rotation is manual: replace the value in the systemd `EnvironmentFile`, restart the unit, update the caller.

---

## 5. Configuration

`config/api_settings.yaml` (module-owned, per repo convention):

```yaml
# API Module Configuration Settings (v1.0)

service:
  # The bind address is a fixed constant (127.0.0.1) enforced in code (§3);
  # it is deliberately absent here. Only the port is configurable — the
  # nginx upstream in §3 must be kept aligned with it.
  port: 8010

export:
  # Default publish export root, resolved against the workspace root.
  # Overridden by the API_PUBLISH_EXPORT_DIR environment variable.
  export_dir: "data/publish_export"

freshness:
  # data_may_be_stale flips when now - last_successful_run_at exceeds this.
  freshness_sla_hours: 6

auth:
  # Name of the environment variable carrying the Bearer token.
  token_env_var: "EXOPOLITICS_API_TOKEN"
```

Precedence: environment variable (`API_PUBLISH_EXPORT_DIR`) > `export_dir` config > built-in default. The port comes from config; the bind address is fixed (§3); the token always comes from the environment, never from YAML. Unknown YAML keys are a fail-fast validation error. Duplicate YAML mapping keys are likewise fail-fast — PyYAML's default last-wins behavior is disabled, so a stray key (e.g. a first `service:` block carrying `host:`) can never be silently discarded by a later block.

---

## 6. Freshness Policy

- `data_may_be_stale` is computed **per request** from the live pointer: `now_utc - last_successful_run_at > freshness_sla_hours`. No caching of this judgment, no background monitoring inside the service (operational reporting belongs to `analysis`).
- Default `freshness_sla_hours: 6`. Rationale (proposal §8.4): the observed pipeline cadence is hourly (`exopolitics-pipeline.timer`), and every successful run refreshes `last_successful_run_at` even when content is unchanged — so six hours means six consecutive missed or failed runs before the flag flips. The original 48-hour draft predates the observed cadence and would mask a dead pipeline for two days.
- The threshold is deliberately generous against false positives (a deliberately paused pipeline reports stale, which is honest), and the value is config, not code.

---

## 7. Response Caching Policy

The module owns response caching policy (boundary definition §4 of the proposal). v1 policy:

- Every application response, plus the documented nginx `413`, `429`, `502`, and `504` responses, carries `Cache-Control: no-store`. Low-level HTTP parsing and connection failures that occur before nginx selects `/v1/` are outside the API contract.
- Rationale: the sole v1 caller issues one short batch per day; caching buys nothing and adds an invalidation concern.
- A future generation-scoped cache (e.g. `ETag`) is an additive change allowed under `API_CONTRACT.md` §6, to be introduced only when a second consumer justifies it. Its key must cover the full response identity: `generation`, normalized filters, cursor/page position, `limit`, and the normalized `include` set.

---

## 8. Read-Retry Internals

Implements `API_CONTRACT.md` §5. Per request:

1. Read and validate `current.json` (rules in `DATA_DEPENDENCIES.md` §4). Missing/invalid → `503` + `Retry-After: 30`.
2. Resolve the generation directory, read `<lang>/index.json`, join the page's slugs against `items/`, assemble the response.
3. If any step raises `FileNotFoundError`/`NotADirectoryError` — the generation was swept by retention mid-read — re-read `current.json` and re-run the entire flow **once**. A second failure → `503` + `Retry-After: 30`.
4. A JSON parse error or schema violation in `index.json` or an item file is **not** retried: the generation is immutable, so re-reading cannot help. It maps to `500`.

Memory bounds: `index.json` (~800 KB at 1,000 entries) is read whole — this is bounded by the publish-side `latest_limit` and needs no streaming. `items/` joins touch at most one page of slugs (≤ `limit`, max 500). No structure grows with total archive size. The opt-in `include=bullets` projection adds no read or memory surface: the join already loads the full item records, and the projection only controls serialization into the response.

---

## 9. Failure Model

Fail-stop, mapping to the contract error table:

| Condition | Result |
|:---|:---|
| Config invalid at startup (bad YAML, unknown key, missing token env var, nonexistent explicit export-dir override, existing but unreadable/untraversable export root) | `validate` exits non-zero; `serve` refuses to start |
| `current.json` missing/invalid at request time (including bootstrap) | `503` + `Retry-After: 30` |
| Generation swept mid-read, retry exhausted | `503` + `Retry-After: 30` |
| Malformed data inside a resolved generation (including a malformed `bullets` shape on a requested projection — never silently omitted) | `500` |
| Malformed parameters, unsupported language, invalid/expired cursor | `400` (unsupported language lists the supported codes; expired cursor uses code `cursor_expired`) |
| Missing/invalid Bearer token | `401` + `WWW-Authenticate: Bearer` |
| Request body exceeds the `/v1/` limit | `413`, JSON body — emitted by the nginx edge (§3) |
| Request rate exceeded | `429` + `Retry-After`, JSON body — emitted by the nginx edge, never by this service (§3) |
| nginx cannot reach the API process | `502` + `Retry-After`, JSON body — emitted by the nginx edge (§3) |
| nginx times out waiting for the API process | `504` + `Retry-After`, JSON body — emitted by the nginx edge (§3) |

The service must still start and serve `503`s when the export has never completed — a missing pointer is a runtime state, not a startup failure.

---

## 10. Observability

- uvicorn access logs go to journald via systemd. Log lines must never include the `Authorization` header.
- Each request logs the resolved `generation` at INFO (one line), so a caller report can be tied to exactly one export generation when debugging.
- Startup logs the configured export root, the bound address, and whether a valid pointer is currently resolvable — never the token.
- No metrics endpoint in v1 (single-caller scope discipline; operational aggregation belongs to `analysis`).
