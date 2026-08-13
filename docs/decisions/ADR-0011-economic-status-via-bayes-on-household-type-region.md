# ADR-0011 · 2026-06 · Economic status via Bayes on household-type × region
- **Status:** active
- **Context:** Mapping economic status 1:1 from the income €-class is a weak predictor.
- **Decision:** Determine `economic_status` (5 BMDV classes) from the stronger Haushaltstyp×Region
  predictor by Bayes `P(status|hhtype,region) ∝ P(hhtype|status,region)·P(status|region)`, with the
  Niedersachsen Bundesland table as base and the national RegioStaR-7 raumtyp table as a within-NDS
  tilt; then re-derive `household_income` from the sampled status. Flag `status_from_hhtype`
  (code default true; OFF reproduces commit c65399d byte-identically).
- **Rationale:** Haushaltstyp×Region is the much stronger signal; the raumtyp table is national so it
  is applied only as a within-NDS tilt, not as a base (CLAUDE.md "Economic status from MiD").
- **Consequences:** Income and status agree by construction; primary/fallback classification rate is
  logged (no silent fallback).
- **Evidence:** CLAUDE.md "Economic status from MiD household-type × region";
  `tests/test_status_from_hhtype.py`; PROJECT_STATUS.md §2.2 (MiD status×hhtype×region).

