# ADR-0020 · 2026-06-18 · Fleet internal consistency v2 + income-coupled vehicle age
- **Status:** active
- **Context:** The first fleet draw produced physically inconsistent vehicles (e.g.
  "diesel Lamborghini") and an income-blind vehicle age.
- **Decision:** Add a brand-level HSN/TSN feasibility fallback (consistency v2) and an
  income-coupled vehicle-age tilt (`fleet_age_income_coupling`, AgeIncomeModel with a fallback ladder).
- **Rationale:** The consistency v2 kills impossible brand×powertrain×fuel combinations and forces
  Tesla→BEV; the income-age coupling asserts the MiD income-age gradient *spread* (not an absolute
  KBA-anchored level), so the OFF golden stays byte-identical (commits `42132d4`, `4ed63d3`).
- **Consequences:** Realistic, internally consistent per-household fleet; fleet evaluation panel
  added to population_validation.
- **Evidence:** spec `docs/superpowers/specs/2026-06-18-fleet-vehicle-consistency-and-income-age-design.md`;
  PR #12 (consistency, merged 2026-06-18) and PR #13 (income-age, merged 2026-06-18);
  PROJECT_STATUS.md §2.3.

