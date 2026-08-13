# ADR-0002 · 2026-06-15 · Three population-synthesis workflows (`population.method`)
- **Status:** active
- **Context:** The inherited path is a single IPF-from-census synthesis. The project needed to
  fold PopulationSim-based synthesis (from a separate `popsimprep` repo) into the synpp pipeline
  while keeping the legacy IPF path intact and reproducible.
- **Decision:** Add a `population.method` switch with three paths: `simple_ipf_open` (legacy
  IPF, the default), `popsim_open` (PopulationSim on Zensus controls), and `popsim_mid`
  (PopulationSim + MiD 2023 donor). The all-features production configs use `popsim_mid`.
- **Rationale:** Mirror the proven eqasim ENTD pipeline structure exactly (reuse its helpers/
  schema/vocab) rather than approximate it; keep alternative paths flag-selected for
  reproducibility (memory `project-popsim-three-workflows`).
- **Consequences:** `braunschweig/popsim/` becomes the production synthesis path; downstream
  gravity/location/mode-choice all run on the popsim output, so popsim is "the foundation"
  (re-tuning popsim forces re-tuning gravity — PROJECT_BACKLOG.md §1).
- **Evidence:** PR #1 "Feature/population method workflows" (merged 2026-06-15); commit
  `cd9d217`; PROJECT_STATUS.md §2.1.

