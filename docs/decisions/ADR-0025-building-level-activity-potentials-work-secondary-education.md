# ADR-0025 · 2026-06-25 · Building-level activity potentials (work/secondary/education) — REPLACE
- **Status:** active
- **Context:** Without building-level potentials, every activity is placed at a zone centroid or a
  uniform random building, so large offices/shops/schools do not attract proportionally more trips.
- **Decision:** Redistribute work, secondary, and education locations to individual OSM/ALKIS
  buildings weighted by a floor-area-based activity potential (from the TUBS-IVS Activities-and-
  Potentials pipeline). For work and secondary the building set REPLACES the candidate set (real
  computed `potential_work`/`pot_*` from the parquet); education ATTACHES within the assigned
  facility. Flags `work_/secondary_/education_building_potentials` (OFF byte-identical).
- **Rationale:** A mid-session pivot chose REPLACE over the earlier ATTACH strategy (ADR-0037);
  aggregate controls (GENESIS SvB, OD flows, NDS enrollment) remain authoritative — potentials only
  govern within-zone/within-school placement (`docs/features/building-potentials.md`).
- **Consequences:** `area*floors` becomes only the OFF/legacy path; real `potential_work` Census-SvB
  cross-check printed. Reshaped within-zone placement (which made the old "0.47" commute figure stale).
- **Evidence:** spec `docs/superpowers/specs/2026-06-25-building-activity-potentials-design.md`;
  PR #16 (merged 2026-06-25) + PR #17 (Copilot follow-up); `docs/features/building-potentials.md`;
  PROJECT_STATUS.md §2.4.

