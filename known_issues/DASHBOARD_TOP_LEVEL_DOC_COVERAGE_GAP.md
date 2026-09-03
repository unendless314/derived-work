# Dashboard Top-Level Documentation Coverage Gap

**Owner:** top-level documentation owner
**Filed by:** `api` module top-level doc sync work (2026-09-03)
**Date:** 2026-09-03
**Status:** Open — deferred; current development focus is the `api` module

## Background

The `dashboard` module is fully implemented (`modules/dashboard/src/`, `tests/`,
and a complete module doc set: README, DASHBOARD_DESIGN.md, DATA_CONTRACT.md),
and unlike the `api` module its ownership boundary is already locked at the top
level:

- `docs/MODULE_BOUNDARIES.md` §3.9
- root `AGENTS.md` Architecture & Ownership Rules

However, several top-level documents still do not reflect the module's
existence:

1. **`docs/SYSTEM_OVERVIEW.md`** — the §2 system-shape diagrams show the
   read-only sidecar path ending at `analysis -> reports/analysis/` with no
   `dashboard` consumer, and the §6 module-role list (§6.1–§6.9) has no
   `dashboard` entry.
2. **`docs/DATA_LIFECYCLE.md`** — §10 side-output lifecycle records `analysis`
   writing `reports/analysis/` but not `dashboard` reading those JSON reports
   as the terminal consumer.
3. **`docs/IMPLEMENTATION_ROADMAP.md`** — no phase or work item covers the
   dashboard (Phase 7 covers `analysis` only), and §6 "Immediate Next Step"
   predates the dashboard's existence.
4. **`docs/README.md`** — the closing note enumerates the analysis integration
   across the doc set but does not mention dashboard coverage.

## Scope of the Eventual Fix

Contract-level, concise additions only, matching how `analysis` was integrated
into the top-level set:

- `SYSTEM_OVERVIEW.md`: extend the sidecar path
  (`analysis -> reports/analysis/ -> dashboard`) and add a §6.x role entry
  (read-only rendering of analysis JSON reports; no DB access, no metric
  recomputation).
- `DATA_LIFECYCLE.md`: record `dashboard` as the terminal read-only consumer
  of analysis report files.
- `IMPLEMENTATION_ROADMAP.md`: record how the dashboard landed (retrospective
  note or phase entry — decide when picked up).
- `docs/README.md`: extend the closing note to cover dashboard.

Module-internal details (Streamlit layout, component map, schema-version
policy) stay in `modules/dashboard/docs/` and should not be promoted.

## References

- [docs/MODULE_BOUNDARIES.md](../docs/MODULE_BOUNDARIES.md) §3.9
- [modules/dashboard/docs/README.md](../modules/dashboard/docs/README.md)
