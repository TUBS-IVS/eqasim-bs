# ADR-0004 · 2026-06 · Joint age×household-size margin (#3)
- **Status:** active
- **Context:** A flat size margin balances size independently of age, so the IPF would invent
  the joint distribution (not knowing large households skew toward school-age children while
  1-person households skew elderly).
- **Decision:** Add the observed age×size correlation to the IPF at Kreis resolution over coarse
  age groups `(15,30,40,50,60)`, 2D-raked to stay consistent with both the population age and the
  size margin (so it cannot make the IPF infeasible). Flag `ipf.use_joint_age_size_margin`,
  source Zensus 2022 1000A-3082.
- **Rationale:** All age-group edges are native ALTKL2 band edges, so aggregating the Zensus
  joint never splits a band (no assumption); the refined `[30,40)/[40,50)` split pins family-size
  households the old `[30,60)` group could not (`docs/features/household-synthesis.md`).
- **Consequences:** Once the composition routing fix (ADR-0005) is in place, the refined bounds
  reduce the parent-child gap>50 share from 2.70% to 0.77%; structural zero (children in 1-person
  HH) held at exactly zero so the IPF does not diverge.
- **Evidence:** `docs/features/household-synthesis.md`; `tests/test_joint_age_size.py`;
  PROJECT_STATUS.md §2.1 (Zensus 1000A-3082).

