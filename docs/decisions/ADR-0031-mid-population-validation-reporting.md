# ADR-0031 · 2026-06-07 · MiD + population validation reporting
- **Status:** active
- **Context:** Runs need reproducible validation against the committed reference data.
- **Decision:** Add an MiD-validation report (`analysis/run_mid_validation.py` vs MiD
  P9/P12_1/P13/P17_1), a combined full analysis (`run_full_analysis.py`), a PopulationSim-style
  population validation (`analysis/population_validation/` vs Zensus: controls/quality/geo), and an
  education enrollment validation (vs LSN capacity).
- **Rationale:** Validation against committed references is mandatory (CLAUDE.md); population
  validation mirrors PopulationSim control validation (spec 2026-06-07).
- **Consequences:** `report.json`/`summary.md`/figures per run; default-on inside full analysis.
- **Evidence:** spec `docs/superpowers/specs/2026-06-07-population-validation-design.md`;
  `docs/features/run-analysis.md`; `tests/test_run_mid_validation.py`; PROJECT_STATUS.md §2.7.

