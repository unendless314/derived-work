# Documentation Set

This directory contains the active top-level planning documents for the rewritten system.

Principles:

- keep only current source-of-truth contracts here
- move superseded planning into `docs_archive/`
- define module boundaries before implementation details
- define storage and retention semantics before database schema details
- define the top-level boundary and positioning for read-only cross-module analytics

Current rewrite order:

1. `PRD.md`
2. `SYSTEM_OVERVIEW.md`
3. `DATA_LIFECYCLE.md`
4. `STORAGE_AND_RETENTION.md`
5. `MODULE_BOUNDARIES.md`
6. `CANONICAL_ENTITY_CONTRACT.md`
7. `MULTILINGUAL_CONTENT_STRATEGY.md`
8. `IMPLEMENTATION_ROADMAP.md`

Note: The active documentation set now also covers the integration positioning for the read-only downstream sidecar analysis module (see SYSTEM_OVERVIEW.md, MODULE_BOUNDARIES.md, CANONICAL_ENTITY_CONTRACT.md, DATA_LIFECYCLE.md, STORAGE_AND_RETENTION.md, and IMPLEMENTATION_ROADMAP.md), its terminal read-only consumer, the dashboard module (see SYSTEM_OVERVIEW.md, MODULE_BOUNDARIES.md, DATA_LIFECYCLE.md, and IMPLEMENTATION_ROADMAP.md), and the read-only api query layer for the deep-reader agent over publish exports (see SYSTEM_OVERVIEW.md, MODULE_BOUNDARIES.md, DATA_LIFECYCLE.md, and IMPLEMENTATION_ROADMAP.md).
