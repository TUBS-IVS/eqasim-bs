# ADR-0032 · 2026-06-23 · Integerizer-quality analysis (per-cell error map)
- **Status:** active
- **Context:** PopulationSim hits the household total exactly but may squeeze out large/rare
  household types; this needs to be visible per cell.
- **Decision:** Add an integerizer-quality report (per-control split, per-cell %-dev, GPKG 100m-cell
  map, CLI) under `analysis/integerizer_quality/`.
- **Rationale:** Makes the 100m composition under-fit measurable (it later showed ZENSUS100m mean
  |%dev| 6.04%, max 27.87% — the evidence behind the rejected importance calibration, ADR-0039)
  (PROJECT_BACKLOG.md step-1b).
- **Consequences:** Provided the measurement that proved importance tuning would not help (controls
  already hit) and is donor-bound.
- **Evidence:** spec `docs/superpowers/specs/2026-06-23-integerizer-quality-analysis-design.md`;
  commits `7b09658`, `f9c9417`; PROJECT_STATUS.md §2.7.

