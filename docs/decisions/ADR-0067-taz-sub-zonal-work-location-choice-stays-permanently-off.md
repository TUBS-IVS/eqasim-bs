# ADR-0067 — TAZ sub-zonal work-location choice stays permanently OFF (superseded in practice by building potentials); TAZ issues closed (2026-07-16)

- **Status:** accepted 2026-07-16. No code or output change (the feature was already flag-gated default OFF and
  OFF in all four real-data configs). Extends ADR-0050 (which had parked the friction re-fit and left the
  "validate flag-ON at scale" step open); this ADR closes that step out as won't-do. Number is 0067 to avoid
  colliding with ADR-0066 (svb_wohn) already on `origin/main`.
- **Context:** TAZ work-location choice (VISUM Verkehrszellen, issue #79) was built to give the WORK gravity a
  sub-commune resolution so intra-city commutes in the kreisfreie Staedte (BS/SZ/WOB) do not collapse to the
  commune centroid. Phase 1+2 merged (PR #85) but the flag was never enabled in any production run; Phase 3
  friction was built and parked (ADR-0050). In the meantime the building-level activity potentials (PR #16, ON
  in all production configs) took over the same job from the other side: they place work at real OSM/ALKIS
  buildings weighted by `potential_work`, so the within-commune work location — and thus the intra-city commute
  distance — is already resolved below the commune without TAZ.
- **Decision:** keep TAZ **permanently OFF**. Do NOT run the outstanding same-commit 25%/100% A/B; the decision
  is that the building potentials are the intra-city resolution mechanism and TAZ is not pursued further. The
  TAZ code stays merged on `main` behind `taz_work_location_choice: false` (OFF byte-identical, zero runtime
  cost) and is reactivatable if a future measurement ever shows a real intra-city gap.
- **Why (measured / traceable):** (1) the mechanisms are complementary but the building potentials already
  cover the intra-city gap TAZ targeted — gravity sets *which zone* (with distance decay), potentials set
  *which building* within it (attraction mass), and with potentials ON the work building is a real scattered
  location, not the centroid. (2) On the current 100% `popsim_mid` population the aggregate commute-distance
  distribution (flag-OFF, building-potentials ON, ZGB-resident) already fits MiD P13 (EMD ~0.054, below the
  0.08 band; ADR-0050). (3) The only signal *for* TAZ was a 1% A/B (EMD 0.057 -> 0.033) that is noisy and
  cross-commit; a clean same-commit re-measurement was judged not worth it against an already-passing model.
  (4) The RVB VISUM Verkehrszellen are proprietary / local-only / non-publishable — a reproducibility liability
  that the open OSM/ALKIS building-potentials path avoids.
- **Consequence:** four TAZ issues closed on `TUBS-IVS/eqasim-bs` — **#79** completed (feature merged, kept
  flag-gated OFF), **#83 / #95 / #80** not planned (scale validation, validation map, open-data pseudo-zone
  alternative). The parked branch `feature/taz-gravity-calibration` remains as backup only.
- **Evidence:** ADR-0050; `docs/features/taz-work-location.md`; `docs/features/building-potentials.md`;
  issues #79/#80/#83/#95 (closed 2026-07-16); memory `project-taz-subzonal-work-location`,
  `project-building-activity-potentials`.

---

