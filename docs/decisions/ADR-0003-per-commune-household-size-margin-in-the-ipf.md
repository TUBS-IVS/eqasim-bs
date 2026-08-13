# ADR-0003 · 2026-06 · Per-commune household-size margin in the IPF
- **Status:** active
- **Context:** The base IPF balanced persons to census but did not pin household sizes per
  commune, so the synthesised size distribution drifted from Zensus.
- **Decision:** Add a flag-gated per-commune household-size margin
  (`ipf.use_household_size_margin`) from Zensus 2022 1000A-2081.
- **Rationale:** Anchor the synthetic size distribution to a committed Zensus table; the joint
  age×size margin (ADR-0004) and age-aware composition (ADR-0005) build on it
  (`docs/features/household-synthesis.md`).
- **Consequences:** Prerequisite for the joint age×size margin; OFF path byte-identical.
- **Evidence:** `docs/features/household-synthesis.md`; PROJECT_STATUS.md §2.1 (Zensus 1000A-2081).

