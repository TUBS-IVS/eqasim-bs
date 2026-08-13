# ADR-0049 — Wolfsburg commute "misfit" is an unreliable reference, not a model bug; sub-zonal (TAZ) is the real lever

- **Date:** 2026-06-30
- **Decision:** Do NOT calibrate the gravity to fix the Wolfsburg per-Kreis commute EMD (0.209
  vs MiD P13). A systematic-debugging pass ruled out every candidate cause and showed the model
  is defensible; the **per-Kreis MiD P13 target for Wolfsburg is n_weighted=39 / n_unweighted=126**
  and is **inconsistent with the authoritative BA Pendleratlas full count**. The genuine,
  scientifically-defensible improvement lever is **sub-zonal resolution** (eqasim IRIS-analog):
  run the work location choice at VISUM-Verkehrszellen (TAZ) resolution so the gravity forms
  distances *inside* the kreisfreie Staedte (BS/SZ/WOB = 1 Gemeinde each).
- **Why (ruled out, all checked against real data):** friction is moot (1 Gemeinde -> gravity
  cannot shape intra-city); real VW-concentrated worker data barely moves it (0.209->0.19; homes+
  jobs co-located ~4 km, centroids 1.2 km); the in/out split **= BA exactly** (78.2% intra, svb
  53,015 / out 11,550); Hannover/Berlin out-commuters ARE simulated (external workplaces ~7.4% ~= BA)
  and ARE in the EMD; excluding the far tail makes it WORSE (the gap is missing 10-30 km, not the
  tail); routing would need ~4x detour (RS7-72 circuity ~1.2). Arithmetic: BA caps out-commuting
  at 22% but MiD wants 53% at 10-30 km -> the surplus can be neither out-commuters nor intra-city
  in a 15-km town -> the n=39 MiD sample is unrepresentative. Calibrating to it would be overfitting
  to noise (forbidden: anti-overfitting / no-invented-references).
- **Consequence:** (1) evaluate per-Kreis fit only where `n_weighted >= ~80`, else use ROBUST
  references (ZGB-aggregate n=1583, per-RS7) -- drives the distance_fit module's n-awareness
  hardening; (2) TAZ sub-zonal work location choice approved (issues #79 / #80) -- flag-gated
  default OFF, TAZ data local-only (proprietary VISUM), reuse the zone-agnostic eqasim functions
  (distance_matrix via the zone stage, gravity / candidates / define_distance_ordering), BA stays
  the Kreis-level anchor.
- **Evidence:** this session's debugging; `eqasim-data/data/braunschweig/mid/mid2023_P13.csv`
  (WOB row n_weighted=39); BA Pendleratlas + census employment stages (svb/out); specs
  `docs/superpowers/specs/2026-06-29-distance-fit-diagnostics-design.md` and
  `2026-06-30-taz-subzonal-work-location-choice-design.md`; memory
  `feedback-robust-reference-not-perkreis-noise`, `project-taz-subzonal-work-location`.

