# API Module

**Document version:** v1.3
**Updated:** 2026-09-03
**Status:** Approved for implementation (owner decision, 2026-09-02); documentation baseline complete, top-level doc sync landed 2026-09-03; v1 implemented (src/, config/, tests/) with the §4 contract-derived test suite green

---

## 1. Module Positioning

`api` is a read-only query layer over publish exports, positioned as a downstream-only sibling of `site`:

```
publish export ──> site  (for humans)
              └─> api   (for the deep-reader agent)
```

v1 serves exactly one consumer: an AI deep-reader agent on the owner's home machine, running one short batch query per day against the production VPS to assemble the daily worth-reading list. Both consumers read only publish-layer outputs through the `current.json` pointer; neither touches canonical storage.

**Product constraint (owner decision, 2026-08-17):** the site is a breaking-news aggregator with strong timeliness; deep-reader queries concern the recent past only ("today", "this week"). v1 is scoped to the recent window (`index.json`, latest 1,000 items) — a product definition, not a limitation.

### 1.1 Boundary Summary

Full boundary text in `MODULE_PROPOSAL.md` §4 (mirrored as `docs/MODULE_BOUNDARIES.md` §3.10). In short:

- **Owns:** the `/v1/` wire contract, query semantics (event-time filtering, ordering, cursor pagination, coverage/freshness declaration), transport and deployment form, response caching policy.
- **May read:** publish-layer outputs only (`data/publish_export/` via the `current.json` pointer).
- **Must not own:** canonical DB access, export shape/layout/slugs (`publish`), pipeline execution, lifecycle state changes, the supported language set (authoritative source: `current.json`).

## 2. Key Responsibilities

1. Serve `GET /v1/articles` with event-time (`source_published_at`) range filtering, total stable ordering, generation-bound cursor pagination, and an opt-in `include=bullets` projection (default off).
2. Declare coverage window and pipeline freshness on every response, so the agent can distinguish "no news" from "pipeline stale".
3. Enforce single-token Bearer authentication as an anti-abuse control (the content is public; this is not a confidentiality boundary).
4. Fail stop with the contract's error semantics (`API_CONTRACT.md` §4 error table); never fall back to stale generations, flat layouts, or directory scanning.

## 3. Document Map

- [MODULE_PROPOSAL.md](./MODULE_PROPOSAL.md): historical decision record — rationale, boundary proposal, and the owner decisions that shaped v1 (approved 2026-09-02).
- [API_CONTRACT.md](./API_CONTRACT.md): the v1 wire contract — endpoint, parameters, response schema, ordering/pagination, coverage and freshness semantics, error table, read-consistency protocol, versioning policy.
- [DATA_DEPENDENCIES.md](./DATA_DEPENDENCIES.md): everything the module reads from the publish layer — export layout facts, adapter boundary, language authority, pointer validation rules, export-root location.
- [EXECUTION_POLICY.md](./EXECUTION_POLICY.md): service form, network exposure (nginx + TLS, code-enforced loopback), authentication, configuration, freshness threshold, caching policy, retry internals, failure model, observability.
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md): module layout, development phases, and the contract-derived test checklist.

## 4. Config Map

- `config/api_settings.yaml`: service settings — `port`, `export_dir`, `freshness_sla_hours`, `token_env_var`. The bind address is a fixed loopback constant enforced in code, deliberately not configurable (`EXECUTION_POLICY.md` §3); config details in §5 of the same document.
- `requirements.txt`: FastAPI/uvicorn/PyYAML pins — the module installs standalone in a clean environment.
- Environment variables: `API_PUBLISH_EXPORT_DIR` (export root override, mirrors the site's convention), and the Bearer token variable named by `token_env_var` (default `EXOPOLITICS_API_TOKEN`) — the token itself never enters the repository.

## 5. Minimal CLI Usage

Validate configuration and export-root assumptions:

```text
python -m modules.api.src.cli validate
```

Start the service (fixed loopback binding, behind the nginx TLS reverse proxy):

```text
python -m modules.api.src.cli serve
```
