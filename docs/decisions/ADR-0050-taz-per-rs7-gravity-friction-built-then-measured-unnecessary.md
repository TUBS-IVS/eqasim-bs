# ADR-0050 — TAZ per-RS7 gravity friction: built, then measured unnecessary; commute distribution already fits; validate flag-ON at scale instead

- **Date:** 2026-07-01
- **Decision:** Do NOT pin the TAZ per-RS7 / per-band `gravity_friction_factors` calibration.
  The machinery was fully built and reviewed (branch `feature/taz-gravity-calibration`, 6 commits
  `c8655b1..3c2ebb5` — a `--taz` mode in `scripts/calibrate_gravity_distribution.py` re-fitting friction
  on the TAZ work-OD via `compute_work_od` + TAZ-aware `_calibrate`, work-pass-scoped so it cannot leak
  into the education Gemeinde pass), but the pre-calibration measurement showed friction is **not
  needed**. The branch is **PARKED (pushed to the fork as backup, not merged)** as gated-off infra,
  reusable only if a future measurement shows a real gap. The remaining Phase-3 work is to **validate the flag-ON TAZ
  feature at scale** (run the 100% population with `taz_work_location_choice: true`,
  `matsim_last_iteration: 0`), not to calibrate.
- **Why (measured, traceable references only):** (1) **Mechanism** — eqasim's two-stage location
  choice was verified adversarially to be a BIJECTION: `candidates.py` draws exactly one candidate zone
  per person from the (gravity-synthesised) OD, and `locations.py::define_distance_ordering` only
  RE-PAIRS candidates to persons to match each survey `commute_distance`. So the AGGREGATE distance
  distribution is set by the gravity candidate pool; friction is a legitimate lever on it, but the
  per-person matching does not change the aggregate. (2) **Fit** — on the CURRENT 100% `popsim_mid`
  population (flag-OFF, ZGB-resident-filtered commutes vs the committed `mid2023_P13.csv`), the
  aggregate EMD is **~0.054** (< the ~0.08 no-recalibration band). The earlier "0.47 FAIL" was a stale
  pre-building-potentials number. So the aggregate already fits — recalibrating would be fixing a
  working model. (3) **WOB** — per-Kreis Wolfsburg EMD ~0.21 is the n=39 noise outlier of ADR-0049, not
  a target. (4) **1% flag-ON A/B first-look** — flag-ON IMPROVES the aggregate (EMD 0.057 -> 0.033) by
  correcting commune-centroid over-concentration of <=5 km commutes toward P13; the compact-city
  intuition was backwards (centroids were too short, TAZ lengthens within-commune commutes to realistic
  building distances).
- **Consequence:** friction branch parked (infra only); Phase-3 becomes a **validation run** of the
  merged flag-ON TAZ (issue #83 re-scoped) + a spatial validation map (new issue); the flag-ON 100%
  run needs a multi-hour popsim rebuild because origin/main's popsim/secondary sources differ from the
  commit that built the existing 24G flag-OFF cache (the "cheap cache prime" premise is dead, per the
  Phase-3 measure-first note in `project-taz-subzonal-work-location`).
- **Evidence:** this session's measurement; branch `feature/taz-gravity-calibration` @ `3c2ebb5`
  (+ SDD ledger `.superpowers/sdd/progress.md`); `synthesis/population/spatial/primary/{candidates,locations}.py`;
  `eqasim-data/data/braunschweig/mid/mid2023_P13.csv`; memory `project-taz-subzonal-work-location`,
  `feedback-measure-before-calibrating`, `feedback-robust-reference-not-perkreis-noise`. Follows ADR-0049.

