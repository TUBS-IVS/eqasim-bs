# ADR-0005 · 2026-06-04 · Age-aware household composition (#3b) with children-driven capacity
- **Status:** active
- **Context:** The legacy random within-bucket chunk + independent hh_type draw produced
  implausible "single parents": ~23% of placed children had a youngest household adult 55+ years
  older (mean gap 84 years), because surplus children spilled onto elderly childless-shell adults.
- **Decision:** Replace it with one coupled optimisation pass per `(commune, hh_size)` bucket
  (`form_households_age_aware`, flag `ipf.age_aware_chunking`): hard adult/child composition per
  hh_type, age-gap-minimising couple pairing, children placed by a sorted rank match around a
  target gap drawn `N(31.8, 5.5)`, and `_ensure_child_capacity` grows child-bearing capacity so
  no surplus child lands on an elderly adult.
- **Rationale:** The mother-age-at-birth target 31.8 is Destatis 2024 (committed reference); the
  sorted rank match is the same 1-D optimum as a Hungarian LAP but `O(n log n)` instead of
  `O(n^3)`, essential because formation runs on the full ~1.13M-person population
  (`docs/features/household-synthesis.md`).
- **Consequences:** gap>55 tail drops to ~0.3% (~0.03% with refined bounds), mean gap 39→26;
  `child_parent_age_target_weight=0.85` then lifts the realised mean back to 31.8. No person ever
  dropped; all-children households hard-blocked.
- **Evidence:** spec `docs/superpowers/specs/2026-06-04-age-aware-household-chunking-design.md`;
  plan `2026-06-04-age-aware-household-chunking.md`; `docs/features/household-synthesis.md`;
  `tests/test_household_composition.py`.

