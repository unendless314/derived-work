# System Overview

**Status:** Active rewrite draft  
**Updated:** 2026-09-04

---

## 1. Purpose

This document describes the top-level system shape for the rewritten planning set.

It focuses on:

- processing stages
- storage layers
- module ownership at a high level
- what the public site is allowed to depend on

It does not define module-internal implementation details.

---

## 2. System Shape

```text
External Feed Sources
  -> ingest
  -> canonical storage
  -> classify
  -> curate
  -> translate
  -> publish
  -> site
```

Recognized workflow branch:

```text
curate
  -> edit workflow
  -> human review
  -> translate
```

Read-Only Sidecar Path:

```text
canonical storage -> analysis
ingest configuration -> analysis
analysis -> reports/analysis/
reports/analysis/ -> dashboard  (read-only rendering for operators)
```

Publish Export Consumers:

```text
publish export (live generation via current.json)
  -> site  (public presentation for humans)
  -> api   (read-only query layer for the deep-reader agent)
```

---

## 3. Core Architectural Principles

- canonical storage is the main system record
- publish output is derived and rebuildable
- the public site must not write to canonical storage
- raw input and downstream working text are different representations
- module boundaries should follow ownership of decisions, not convenience of code placement

---

## 4. Canonical Storage Role

Canonical storage exists to keep the durable system record for:

- normalized source items
- sanitized working text
- source health and ingest execution metadata
- classification outputs
- curation decisions
- site-owned edit records
- approved content records (`approved_content_record`) as canonical handoff artifacts assembled from finalized editorial state
- translation outputs (`translation_output`) and metadata (status, fingerprints)
- publishable content references, frozen slugs, and metadata

Canonical storage is not required to retain every raw payload forever.
Retention policy is a separate concern from canonical identity.

---

## 5. Representation Layers

The rewritten system recognizes at least four important content representations:

### 5.1 Raw Feed Representation

- what the feed originally provided
- useful for debugging, validation, and sanitizer evaluation
- not safe as the default downstream LLM input

### 5.2 Sanitized Working Representation

- cleaned text derived from raw feed content
- intended for classification and downstream curation support
- must be contractually defined and predictable

### 5.3 Translation Representation

- structured translated content (display title, short summary, and up to three bullet fields) for each language code
- kept in canonical storage, mapping to the upstream approved content fingerprint

### 5.4 Publish Representation

- approved outputs prepared for public consumption in static file form (multilingual folders, index, feeds)
- derived from canonical records using a unified frozen slug lookup
- carries structured content with semantic bullet keys; localized post presentation labels are applied by `site` at build time, not stored in the export
- emitted as immutable, versioned generation directories under `data/publish_export/generations/`, committed by an atomic `current.json` pointer switch — the publish→site handoff resolves the live generation exclusively through that pointer
- optimized for site rendering rather than operational workflow

---

## 6. Module Roles

### 6.1 `ingest`

Owns:

- source config loading and validation
- feed fetching
- deduplication
- normalized item persistence
- sanitized working text generation
- source health tracking

### 6.2 `classify`

Owns:

- pending item selection (excluding only items where `text_processing_status` is `failed` or `text_processing_reason` is `post_cleanup_empty`; all other items, including normal `low_context` outcomes, enter classify)
- initial topic classification
- confidence and rationale persistence
- generating descriptive signals (descriptors) for downstream curation triage

### 6.3 `curate`

Owns:

- curation decisions (including approval, rejection, manual withdrawal/re-approval, and downstream action decisions)
- curation queue governance
- editorial curation over public exposure

### 6.4 `edit`

Owns when active in the workflow:

- site-owned draft and edited content records
- source linking for edited content
- edit-specific provenance and responsibility metadata

### 6.5 Shared Handoff Capability

The rewrite recognizes one shared capability without promoting it to a formal module yet:

- assembly of finalized curation approvals and finalized edited drafts into a unified `approved_content_record`
- exposure of that mother-draft as a pull-based downstream handoff

### 6.6 `translate`

Owns:

- translation of structured content fields (display title, short summary, and bullet slots)
- content translation versioning and source fingerprint matching
- language coverage status and quality states (pending, completed, failed, stale)
- translation LLM orchestration, prompt template loading, and rate limiting

### 6.7 `publish`

Owns:

- selecting completed translated records (`translation_output`) of actively approved items for export
- generating publish-layer outputs and synchronizing cleanup for withdrawn items
- preserving attribution, disclosure, and unified slug generation
- versioned generation emission: each content-changing run builds a complete immutable generation under `data/publish_export/generations/` and commits it by atomically switching the `current.json` pointer

### 6.8 `site`

Owns:

- public presentation
- UI localization (i18n) and SEO concerns
- routing and page generation
- static rendering concerns

Renders read-only from the live publish generation resolved through `current.json`; no canonical DB access.

### 6.9 `analysis`

Owns:

- read-only analytics computation and report generation
- writing report outputs to `reports/analysis/`

Important rule:

- `analysis` does not own operational decisions or canonical state changes.

### 6.10 `api`

Owns:

- the read-only query contract over publish exports (endpoints, response schemas, query semantics, versioning)
- service transport and deployment form

Serves publish-layer outputs read-only, resolved through the atomic `current.json` pointer; no canonical DB access. v1 serves the owner's deep-reader agent; module contract details live in `modules/api/docs/`.

### 6.11 `dashboard`

Owns:

- read-only rendering of `analysis` JSON reports (`reports/analysis/`) as interactive charts and tables
- dashboard UI configuration and supported schema-version declarations

A pure presentation consumer of the `analysis` JSON contract; no canonical DB access, no metric recomputation. Module contract details live in `modules/dashboard/docs/`.

---

## 7. Out Of Scope For This Layer

This document does not lock:

- exact schema column names
- exact retention durations
- exact sanitization algorithm details
- CLI command layout
- curation UI design
