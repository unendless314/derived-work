# Implementation Roadmap

**Status:** Active planning draft  
**Updated:** 2026-09-03

---

## 1. Purpose

This document defines the recommended order of work for the system modules.

The key principle is:

- lock contracts before rewriting implementation

Because core pipeline contracts (ingest through publish/site) have stabilized, the roadmap prioritizes the integration of the new read-only analysis module.

---

## 2. Phase Order

### Phase 1: Lock Top-Level Contracts

Finish and review the new top-level docs:

- `PRD.md`
- `SYSTEM_OVERVIEW.md`
- `DATA_LIFECYCLE.md`
- `STORAGE_AND_RETENTION.md`
- `MODULE_BOUNDARIES.md`
- `CANONICAL_ENTITY_CONTRACT.md`

Goal:

- remove ambiguity before touching implementation

### Phase 2: Ingest Data Contract

Update ingest module docs to reflect the new model:

- raw input representation
- sanitized working text representation
- retention-governed raw handling
- new source item contract

Goal:

- ensure `ingest` writes the right downstream representation

### Phase 3: Canonical Schema Direction

Update schema planning and module-level storage docs so they express:

- long-term canonical fields
- short-retention raw fields or structures
- sanitization metrics
- explicit downstream text fields

Goal:

- stop overloading ambiguous text columns

### Phase 4: Ingest Implementation

Change ingest code and schema together.

Expected work:

- adjust parsing and persistence path
- create sanitized working text during ingest
- preserve raw input only under the new retention-aware model

Goal:

- make canonical ingest output match the rewritten contracts

### Phase 5: Classify Contracts And Implementation

Update classify docs and code so the module:

- reads sanitized working text
- uses the new low-context rules
- no longer depends on ambiguous raw summary semantics

Goal:

- align classify with the rewritten ingest contract

### Phase 6: Curation, Translation, and Publish Implementation

Refresh and implement:

- curation queue contracts
- translation module contracts and LLM prompt design
- publish export contracts (multilingual static folders, slug rules, and coverage policies)
- edit workflow contracts needed for the immediate post-MVP phase

Goal:

- ensure downstream modules inherit the corrected upstream semantics
- align translation, slug generation, and static JSON output formats with the new multilingual content strategy
- ensure the post-MVP path to edit-assisted publishing is already aligned with the rewritten core pipeline

### Phase 7: Read-Only Analysis Layer (Current Active Focus)

Only after upstream schema, config ownership, and lifecycle contracts are stable:

- lock analysis top-level integration contract
- implement read-only reporting and metrics aggregation against stabilized schema and config contracts

Goal:

- align analytics and reporting tools with stable upstream canonical database schemas

### Phase 8: Read-Only API Layer (Approved 2026-09-02, Awaiting Implementation)

The `api` module specification is approved (owner decision, 2026-09-02) and its module-level documentation baseline is complete (`modules/api/docs/`); top-level contract docs were synced ahead of implementation.

Expected work:

- implement the read-only query service over publish exports (`GET /v1/articles`) per `modules/api/docs/API_CONTRACT.md`
- service deployment, authentication, caching, and freshness signaling per `modules/api/docs/EXECUTION_POLICY.md`

Goal:

- give the owner's deep-reader agent a stable, freshness-signalled query interface over publish exports without exposing canonical storage

---

## 3. Recommended Validation Strategy

Before implementation is considered stable, validate with real source samples:

- inspect noisy feed summaries
- compare raw versus sanitized text side by side
- measure sanitized length and reduction ratio
- check low-context outcomes after cleaning
- verify classification prompt inputs are materially improved

This validation is mandatory because the rewrite exists specifically to correct a real data-quality problem.

---

## 4. Migration Strategy

Recommended strategy:

- prefer reset and rebuild over backward-compatibility work for active code modifications

Reason:

- during active iteration, stored data can be re-fetched if needed
- the `analysis` module operates purely read-only and does not trigger schema migrations on core tables

---

## 5. Deferred Work

Only after the new core pipeline stabilizes should the project decide whether to add:

- a separate raw staging store
- more advanced readability-based extraction
- shared external content retrieval capability
- a separate executable `edit` module

This deferral applies to extracting `edit` as a separately executable module, not to recognizing `edit` as a near-term product capability.

---

## 6. Immediate Next Step

As upstream contracts (ingest through publish/site) have stabilized, the next concrete step should be:

- align `modules/analysis/docs/` and implementation work with the stabilized canonical schema and the recently approved top-level documentation set.
