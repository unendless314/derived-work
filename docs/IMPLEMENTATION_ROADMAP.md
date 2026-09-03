# Implementation Roadmap

**Status:** Active planning draft  
**Updated:** 2026-09-04

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

### Phase 8: Read-Only API Layer (Approved 2026-09-02, Implemented 2026-09-03)

The `api` module is implemented (`modules/api/src/`, `tests/`): the read-only query service over publish exports (`GET /v1/articles`) per `modules/api/docs/API_CONTRACT.md`, with service deployment, authentication, caching, and freshness signaling per `modules/api/docs/EXECUTION_POLICY.md`. Its specification was approved by owner decision (2026-09-02); the module-level documentation baseline and top-level contract docs were synced ahead of implementation.

Goal:

- give the owner's deep-reader agent a stable, freshness-signalled query interface over publish exports without exposing canonical storage

### Phase 9: Dashboard Layer (Completed)

The `dashboard` module is implemented (`modules/dashboard/`): a read-only rendering layer over the `analysis` JSON reports stabilized in Phase 7, presenting pipeline funnel, source health, classification, translation, and curation diagnostics to operators.

Its ownership boundary is locked in `MODULE_BOUNDARIES.md` §3.9: `dashboard` is a pure presentation consumer of the `analysis` JSON contract, with no canonical DB access and no metric recomputation. Module contract details live in `modules/dashboard/docs/`.

Goal:

- give operators interactive visibility into analysis reporting outputs without exposing canonical storage

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

The previously recorded next step — aligning `modules/analysis/docs/` and the analysis implementation with the stabilized canonical schema and the top-level documentation set — is complete; the `dashboard` module has since landed as the read-only rendering consumer of analysis reports (see Phase 9), and the `api` module is implemented (see Phase 8).

The next concrete focus is an owner decision and is not yet recorded here.
