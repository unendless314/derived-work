# `api` Module — Data Dependencies

**Document version:** v1.1
**Updated:** 2026-09-03
**Status:** Active draft (module approved for implementation 2026-09-02; not yet implemented)

---

## 1. Purpose

This document defines everything the `api` module reads from the publish layer and the rules for interpreting it. The wire contract those reads serve lives in `API_CONTRACT.md`; this document owns the adapter's input side. (Extracted from `API_CONTRACT.md` v1.8 §§5–6, with the pointer validation rules added for parity with the other two consumers.)

The module's entire read scope is: `data/publish_export/current.json` plus the single generation directory it references. Nothing else — no canonical database, no other files at the export root.

---

## 2. Data Source and Adapter Boundary

Responses derive exclusively from the **current generation** under `data/publish_export/` (layout per `known_issues/resolved/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` v7, landed as Phase B1 on 2026-08-18):

```text
data/publish_export/
  current.json                 # atomic pointer; readers' only entry point
  generations/<generation>/
    stats.json
    meta.json                  # optional diagnostics
    <lang>/index.json  items/  archives/
```

Verified export layout facts (re-verified 2026-09-02 against the live generation `2026-08-19T08-49-58Z`; content contracts carry over unchanged from the pre-refactor verification on 2026-08-17):

- `index.json` holds the latest 1,000 items as slim entries, **sorted by `source_published_at` descending**; entry keys are exactly `slug`, `display_title`, `summary_short`, `canonical_url`, `source_published_at`, `approved_at`, `published_at` — index entries carry no `source_item_id`.
- `items/<slug>.json` holds full self-contained records carrying `source_item_id`, `downstream_action`, `language_code`, `bullets`, `disclosure_note`, and `author_metadata` in addition to the index fields.
- `bullets` is present on **every** item record: a fixed three-key object `{key_claim, evidence_level, objective_impact}` on `publish_summary` items, `null` on `publish_link` items (re-verified 2026-09-03 against the live snapshot: 1,500 sampled records, zero absent keys, zero other shapes). The API passes this through verbatim under the opt-in `include=bullets` projection; a malformed `bullets` shape is malformed generation data → `500`, never silently omitted.
- `archives/archive_YYYY_MM.json` group by `source_published_at` month.

Given the product constraint (recent-window queries only), the v1 adapter:

1. reads `current.json` and resolves the generation directory (§4),
2. validates the requested language against the pointer's `languages` list (§3),
3. reads `<lang>/index.json` (one file, already in event-time order),
4. filters by event-time range and paginates,
5. joins matched slugs against `<lang>/items/<slug>.json` for the full-record fields (`source_item_id`, `downstream_action`, and `bullets` when the `include=bullets` projection is requested).

It must **not** scan `items/` wholesale and must **not** stitch `index.json` + `archives/`. The archives and any derived full-set indexing are out of scope until a second consumer with historical query demand appears.

---

## 3. Language Support Is Not Hardcoded — and Directories Are Not Evidence

The authoritative source for the supported language set is the **`languages` list in `current.json`**. Directory existence is explicitly **not** authoritative: publish's execution policy (`modules/publish/docs/EXECUTION_POLICY.md` §6.2) states that directory names are not ownership evidence, and residual directories from before a canonical reset may persist. Serving from an unlisted directory risks exposing content that is no longer configured and no longer meets published conditions.

**Real-world instance (observed 2026-09-02):** the export root still carries pre-refactor residue — top-level `en/`, `ja/`, `zh/`, and `stats.json` dated 2026-08-05 — alongside the generation layout. These directories are inert and must never be served. This exact fixture shape (stale unlisted directories beside a valid pointer) is a required test case; see `IMPLEMENTATION_PLAN.md` §4.

If `current.json` is missing or invalid, the API does not fall back to directory scanning — it returns `503` (`API_CONTRACT.md` §5).

---

## 4. Pointer Validation Rules

The pointer contract is identical across all three parties: the writer (`modules/publish/src/generation_store.py`), the site reader (`modules/site/src/utils/export_root.js`), and this module. The api-side validator must enforce, before any value is joined into a path:

- `generation` matches `^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$` (strict Windows-safe id; validated before it ever reaches the filesystem, so no arbitrary string becomes a path component).
- `export_completed_at` / `last_successful_run_at` match `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` and are calendar-valid (the regex pins the shape; a parse round-trip rejects impossible dates such as February 30).
- `languages` is a non-empty list of strings.
- `content_fingerprint` matches `^sha256-exportstate-v1:[0-9a-f]{64}$`.
- the referenced `generations/<generation>/` directory exists and is a directory.

Any violation is a fail-stop: `503` with `Retry-After` per `API_CONTRACT.md` §5. There is no fallback to stale generations, flat layouts, or directory scanning.

---

## 5. Export Root Location

- Default: `data/publish_export/` resolved against the workspace root (repository root).
- Override: the `API_PUBLISH_EXPORT_DIR` environment variable (absolute or cwd-relative path), mirroring the site's `SITE_PUBLISH_EXPORT_DIR` convention. An explicit override that does not exist is a hard configuration error — it must never silently fall back to the default.
- Precedence: environment variable > `config/api_settings.yaml` (`export_dir`) > built-in default. See `EXECUTION_POLICY.md` §5.
