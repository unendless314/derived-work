# Repository Guidelines

## Project Structure & Module Organization
This repository is a phased, modular UAP/UFO aggregation system plan. Top-level contracts live in `docs/`; implementation work belongs in `modules/<module>/` with `docs/`, `config/`, `src/`, and `tests/`.

Planned module sequence:
`ingest -> classify -> curate -> edit (when needed) -> translate -> publish -> site` (with `analysis` operating as a read-only sidecar observer, and `api` serving publish exports read-only to the deep-reader agent as a downstream sibling of `site`)

The active module workspace has been reset. Archived pre-reset module trees live under `modules_archive/`; new implementation work should restart under `modules/` and preserve future module boundaries.

## Build, Test, and Development Commands
There is no repo-wide build or CI pipeline yet. Use lightweight local commands while working:

- `rg --files` to quickly inspect repository contents
- `sed -n '1,200p' docs/README.md` to review the active top-level documentation set before edits
- `git diff -- docs/ modules/ modules_archive/` to verify changes across planning, active module, and archived module paths when structural moves are involved

When executable code is added, prefer module-local commands from `modules/<module>/`.

## Coding Style & Naming Conventions
Use Markdown for planning docs and YAML for configuration. Keep prose direct and use fenced code blocks for commands or layouts. Document headers carry only the current status snapshot (version, updated date, one-line status); per-version change logs belong to git history, not the document.

Naming patterns:

- Canonical specs: uppercase snake case (example: `ARCHITECTURE_NOTES.md`)
- Config files: lowercase with underscores (example: `new_rss_sources.yaml`)

Keep config ownership inside each module (`modules/<module>/config/`). Add a root `config/` only when multiple modules stably share one contract.

## Architecture & Ownership Rules
Respect module boundaries from `docs/MODULE_BOUNDARIES.md`:

- `ingest`: source ingestion and source health
- `classify`: LLM classification contracts
- `curate`: editorial curation, triage, formatting, and routing decisions
- `edit`: edit-draft provenance and responsibility metadata
- `translate`: multilingual translations and display title refinement
- `publish`: export and disclosure emission
- `site`: render published outputs only; no canonical DB writes
- `api`: read-only query layer over publish exports for the deep-reader agent; same consumption scope as `site` (live generation via `current.json`); no canonical DB access
- `analysis`: read-only diagnostics, operational reporting, and metric aggregation
- `dashboard`: read-only rendering of `analysis` JSON reports; no DB access, no metric recomputation

## Testing Guidelines
No automated test suite is committed yet. For new executable code:

- place tests under `modules/<module>/tests/`
- mirror source layout where possible
- use `test_<unit>.py` naming for Python tests

Before opening a PR, validate changed YAML with the intended parser and verify references such as `category_id`, `fetch_group`, and `schedule_class`.

## Commit & Pull Request Guidelines
Follow the existing commit style: short, imperative subjects (example: `Refine architecture planning docs`). Keep subject lines specific and do not end with punctuation.

PRs should include a concise summary, affected paths, rationale for structural moves, and linked docs/issues. Include screenshots only for visual output changes and list deferred decisions.

## Agent Notes
Do not invent undocumented runtime behavior. Keep top-level docs focused on active cross-module contracts, and put implementation details in module docs. Historical planning belongs in `docs_archive/` and historical module snapshots belong in `modules_archive/`; neither should be treated as current source of truth. Any new scaffold, schema, or state transition must update `modules/<module>/docs/` in the same change.
