# ADR-0010 · 2026-06-15 · Income spatial tilt (Nettokaltmiete) — and the zero-rent gate fix
- **Status:** active
- **Context:** Household income within a Kreis was spatially flat; rent data offers a sub-Kreis
  signal. An initial implementation appeared to *flip* the income–rent correlation negative.
- **Decision:** Apply a within-Kreis income tilt by Nettokaltmiete (flag `popsim.income_spatial_tilt`,
  INKAR/Zensus rent), mean-preserving. The "flip" was diagnosed as a gate bug — the correlation
  filter did not exclude `rent==0` cells, so the owner-index in zero-rent cells dragged it negative;
  fixed to exclude zero-rent cells (commit `36ee20b`).
- **Rationale:** On non-zero-rent cells the tilt gives ΔPearson +0.032 with the mean preserved;
  a within-Kreis *extra* signal beyond size/tenure/age was deliberately dropped (ADR-0036)
  (memory `project-income-spatial-tilt`).
- **Consequences:** Active on the popsim path; INKAR regional scale 0.88–1.09 (03101=1.0014).
- **Evidence:** merge `c604653`; fix commit `36ee20b`; PROJECT_STATUS.md §2.1 (INKAR/Zensus rent).

---

## Attribute enrichment

