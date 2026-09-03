# Module Boundaries

**Status:** Active rewrite draft  
**Updated:** 2026-09-03

---

## 1. Purpose

This document defines ownership boundaries between modules so the rewritten system does not repeat ambiguous contracts.

It is intended to prevent:

- one module silently redefining another module's data meaning
- state changes being scattered across unrelated code
- storage concerns leaking into downstream logic without contract updates

---

## 2. Boundary Principles

- modules own decisions, not just code locations
- upstream modules must not force downstream modules to interpret ambiguous fields
- downstream modules may rely only on explicitly defined representations
- publish and site layers must remain downstream-only consumers

---

## 3. Module Ownership

### 3.1 `ingest`

Owns:

- source configuration
- feed fetching
- deduplication
- normalized source item persistence
- raw input capture policy
- sanitized working text generation
- text-processing outcome classification (completed, low_context, failed) as part of sanitization output
- source health and fetch execution records

May read:

- source config
- prior source state
- prior dedup markers

Must not own:

- LLM prompts
- topic classification decisions
- human approval decisions
- publish export formatting

Important boundary:

- `ingest` owns the transformation from raw feed input into sanitized working text and the determination of `text_processing_status`
- `ingest` must not leave downstream modules guessing whether a field is raw or cleaned

### 3.2 `classify`

Owns:

- pending queue selection for unclassified items
- topic classification results
- classification rationale and confidence
- machine-generated descriptive signals for downstream curation triage

May read:

- normalized source item metadata
- sanitized working text
- `text_processing_status` and `text_processing_reason` (only for pending-queue selection)
- source URL and timestamp metadata

Must not own:

- raw feed retention policy
- feed fetching
- manual review decisions
- publish-layer output structure
- editorial action selection (e.g. translation, rewrite, or research decisions)

Important boundary:

- `classify` reads sanitized working text and excludes only `failed` items and `post_cleanup_empty` outcomes at queue-selection time using `text_processing_status` and `text_processing_reason`; all other items, including `low_context` ones, enter classification
- `classify` must not create placeholder classification rows; every selected item proceeds to normal classification
- `classify` must not define or propagate downstream mother-draft language semantics.

### 3.3 `curate`

Owns:

- curation queue behavior
- editorial status management (including approval, rejection, manual withdrawal/re-approval, and downstream action selection)
- queue aging and SLA policy
- editorial curation over public exposure

May read:

- source item metadata
- sanitized working text
- classification results
- selected raw evidence when retained and needed for investigation

Must not own:

- feed fetching
- model prompt design
- site rendering

Important boundary:

- Under the current system policy, `curate` outputs are strictly English canonical mother-drafts.

### 3.4 `edit`

Owns:

- site-owned draft content
- source linking for edited content
- edit-specific metadata and responsibility fields
- content review and revision

Must not own:

- source ingest identity
- public export rules
- site rendering
- translation execution

### 3.5 `translate`

Owns:

- translation of structured content fields (display title, short summary, and bullet slots)
- content translation versioning and source fingerprint matching
- language coverage status and quality states (pending, completed, failed, stale)
- translation LLM orchestration, prompt template loading, and rate limiting

May read:

- approved content records (`approved_content_record`, representing either finalized curation outputs or finalized edited drafts)

Must not own:

- editorial curation judgment (whether to publish or rewrite)
- assembly of finalized upstream editorial states into the canonical mother-draft handoff
- static exporter layouts or static file folder writing (owned by `publish`)
- post presentation UI labels for bullet content (owned by `site`)

Important boundary:

- The self-translation bypass mechanism must evaluate target locales against `approved_content_record.content_language_code` only.

### 3.6 `publish`

Owns:

- selection of completed translated records for export
- generation of slug on first publication, which is permanently frozen in canonical storage
- versioned generation emission: immutable, complete-snapshot generation directories under `data/publish_export/generations/<generation-id>/`, committed by an atomic `current.json` pointer switch
- the publish-owned export-root state: `generations/`, `current.json`, the `.staging/` build directory, generation retention (newest 5 generations plus the always-protected live generation), and the `publish_runner.lock` single-writer lock placed next to the canonical DB
- attribution and disclosure emission
- downstream export synchronization and cleanup based on upstream state transitions

May read:

- completed translated records (`translation_output`)
- original source item metadata and canonical URL
- upstream curation decision and approval status

Must not own:

- raw data collection
- classification logic
- translation LLM orchestration or cost decisions
- human editorial judgment or content lifecycle state changes (e.g. withdraw decisions)

### 3.7 `site`

Owns:

- routes
- public presentation
- UI localization (i18n) and SEO concerns
- post presentation labels: the localized UI label texts for structured bullet content, applied only at site build time

May read:

- publish-layer outputs only — the live generation resolved read-only through the atomic `current.json` pointer (generation directories under `data/publish_export/generations/` are immutable; `site` never writes there and has no canonical DB access)

Must not own:

- canonical database writes
- curation state changes
- feed or classification configuration

### 3.8 `analysis`

Owns:

- read-only analytics computation and report generation
- cross-module operational analysis outputs

May read:

- canonical operational records
- ingest-owned source configuration metadata

Must not own:

- canonical DB writes
- source config mutation
- pipeline execution
- publish-layer or site-layer output writing

Important boundary:

- `analysis` functions as a downstream observer / sidecar.
- `analysis` may write derived report files to `reports/analysis/`.
- `analysis` is not a gating stage for canonical state transitions or public publishing.

### 3.9 `dashboard`

Owns:

- rendering of `analysis` JSON reports as interactive charts and tables
- dashboard UI configuration and supported schema-version declarations
- report-file loading, validation, and caching behavior

May read:

- JSON report files under `reports/analysis/` only

Must not own:

- canonical DB access or writes
- metric computation or re-derivation
- analysis report generation or pipeline execution
- operational module configuration

Important boundary:

- `dashboard` is a pure presentation consumer of the `analysis` JSON contract; it must never open `canonical.db` or recompute metrics from raw records

### 3.10 `api`

Owns:

- the external read-only contract: endpoints, response schemas, and versioning (`/v1/` prefix)
- query semantics: event-time (`source_published_at`) range filtering, stable ordering, cursor pagination, coverage and freshness declaration
- transport and deployment form (local service, bind address, port)
- response caching policy

May read:

- publish-layer outputs only (`data/publish_export/`, via the `current.json` pointer) — identical consumption scope to `site`

Must not own:

- canonical DB access or writes
- export shape, generation layout, or slug generation (owned by `publish`)
- pipeline execution
- content lifecycle state changes (e.g. withdraw decisions)
- the set of supported languages (authoritative source: `current.json`; directory existence is not evidence)

Important boundary:

- `api` is a downstream-only sibling of `site`; both resolve the live publish generation read-only through the atomic `current.json` pointer

---

## 4. Shared Capability Rules

Some capabilities may later be shared without becoming formal modules immediately.

Example candidate:

- external page retrieval or enrichment
- finalized-content assembly / handoff generation

For the current rewrite, `approved_content_record` is treated as a canonical handoff artifact rather than as a responsibility semantically owned by `curate`, `edit`, or `translate` alone.

Current direction:

- finalized curation approvals and finalized edited drafts are normalized into the same `approved_content_record` contract
- downstream modules consume that handoff artifact by pull, but must verify active publish eligibility from the upstream curation decision
- `approved_content_record` serves as a persistent handoff and cache anchor rather than a dynamic publish/unpublish toggle
- this assembly step is recognized as a shared capability, not yet a formal standalone module

Criteria for becoming shared:

- used by multiple modules
- stable enough to justify a common contract
- no longer just an implementation detail of one module

Until then, avoid inventing heavyweight shared systems too early.

---

## 5. Decisions Locked By This Rewrite

- `ingest` owns sanitization of feed input into downstream working text
- `classify` owns classification, not text cleanup
- `curate` owns curation decision-making
- `approved_content_record` functions as a canonical handoff artifact assembled from finalized upstream editorial states
- `translate` owns content translation and fingerprinted lifecycle
- `publish` owns export shape, slug generation, and the versioned generation/pointer emission layout (`generations/` + atomic `current.json`)
- `site` is a pure downstream consumer
- post presentation labels are owned by `site` and applied at build time; canonical records and publish exports carry label-free structured content
- `analysis` functions as a read-only sidecar observer that does not affect canonical state transitions
