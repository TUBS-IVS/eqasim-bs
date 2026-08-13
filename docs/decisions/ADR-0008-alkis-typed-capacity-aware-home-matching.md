# ADR-0008 · 2026-06-17 · ALKIS-typed, capacity-aware home matching
- **Status:** active
- **Context:** The area-weighted home draw ignored both the household's `building_type_3class`
  (EFH/MFH/sonstiges) and the ALKIS building function/capacity, so EFH households landed on MFH
  footprints and vice versa; a 400m² area cap dropped exactly the apartment blocks MFH households
  should live in.
- **Decision:** Match households to buildings using the household building type and ALKIS-typed
  footprint capacity within each cell, producing the best realistic household↔building combination
  (data-driven; ZGB-8 scope only, national generalisation explicitly out of scope).
- **Rationale:** Rich per-cell Zensus 2022 building/dwelling/size data is now in the prepared
  parquet that was unavailable when the original placement was written (spec §1).
- **Consequences:** Removes the type-fidelity defect and the 400m² cap workaround.
- **Evidence:** spec `docs/superpowers/specs/2026-06-17-alkis-typed-home-matching-design.md`;
  PR #14 "Feature/alkis typed home matching" (merged 2026-06-18); PROJECT_STATUS.md §2.1.

