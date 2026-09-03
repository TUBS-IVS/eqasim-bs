# ADR-0103 · 2026-09-03 · Decision per calibration layer after the SrV distance baseline
- **Status:** active
- **Context:** The spec of 2026-09-03 (SrV distance-distribution calibration, approach A, issue
  #357) pre-registered the rule implemented in `braunschweig.calibration.decision`: a cell (home
  Kreis, or the ZGB aggregate) is a **gap** when its band-share EMD exceeds `0.08` AND exceeds
  that cell's SrV bootstrap noise floor; a calibration layer is **built** iff a home Kreis with
  at least 200 SrV reference persons gaps, or the ZGB aggregate gaps. The rule was fixed BEFORE
  the numbers were seen, precisely so a layer that already matches gets closed rather than built
  (the measure-before-calibrating lesson of ADR-0041). The reference is the one adopted in
  ADR-0102. The baseline measurement is the run
  `srv-primary-distance-baseline-2026-09-03`: the analysis stage
  `braunschweig.analysis.synthesis.commute_distance_by_kreis` run against the cached 100%
  population of `100pct-allfeat-i329-2026-08-24` (304,900 workers, 178,763 pupils/students),
  model distances = realised euclidean home->activity distance x a 1.3 detour factor.
- **Decision:**
  - **Layer 1 -- per-Kreis commute friction, inter-Gemeinde (#359): BUILD.** The rule reports
    `build: gap ... in decisive cell(s) ['03101', '03103', '03153', '03157', '03158', 'zgb']`.
    The ZGB aggregate itself gaps (EMD 0.085 against a noise floor of 0.013 on 2,593 SrV persons),
    and five of eight Kreise gap, worst Goslar 03153 (EMD 0.162, floor 0.028) and Peine 03157
    (0.114, 0.024).
  - **Layer 2 -- SrV-conditioned per-person commute distances, intra-Gemeinde (#360): DO NOT
    BUILD.** The rule reports `do not build: no gap in any Kreis with >= 200 reference persons nor
    in the aggregate`. All nine intra cells classify `ok`; the aggregate EMD is 0.011 against a
    floor of 0.008 on 1,950 SrV persons, and the worst single Kreis is Wolfenbuettel 03158 at
    0.030 (floor 0.022) -- an order of magnitude below the 0.08 threshold. The model's
    within-Gemeinde commute-distance distribution already matches SrV 2023; issue #360 is closed
    by this record rather than scheduled.
  - **Education (#279), per comparable model level:**
    - `kindergarten`: **DO NOT BUILD** -- aggregate EMD 0.067 (floor 0.020) classifies `ok`; four
      Kreise gap but none reaches 200 SrV persons (37-241 per Kreis), so no cell is decisive.
    - `grundschule`: **BUILD** -- aggregate EMD 0.116 (floor 0.021, n_ref 571) gaps; seven of
      eight Kreise gap, worst Wolfenbuettel 03158 (0.225, floor 0.058) and Helmstedt 03154
      (0.211, 0.049).
    - `sekundar_1`: **DO NOT BUILD** -- aggregate EMD 0.025 (floor 0.023) classifies `ok`; only
      03153 (0.086) and 03158 (0.093) gap, neither decisive.
    - `upper_secondary`: **DO NOT BUILD** -- aggregate EMD 0.074 (floor 0.033) classifies `ok`;
      five Kreise gap but all are far below 200 SrV persons (26-120).
    - `university`: the rule reports **BUILD** on the aggregate alone (EMD 0.202, floor 0.083),
      and every per-Kreis cell either gaps (seven, up to 0.482 for Wolfsburg 03103) or is
      `within_noise` (03153, 03158). **This decision is NOT acted on as a calibration mandate**:
      the whole university reference is 142 SrV persons at ZGB level and 7-84 per Kreis, so NO
      university cell reaches the 200-person floor and the "build" verdict rests entirely on the
      aggregate, which the pre-registered rule exempts from that floor. On this evidence the
      level is *not decidable on SrV*; the university slopes stay on the T43/Mikrozensus basis
      and the discrepancy is recorded as an open question for a reference with adequate n. The
      rule is left as pre-registered -- it is not retro-fitted to this outcome -- but its
      aggregate exemption is recorded here as a limitation (see Consequences).
    - The model's `bbs` cohort has no comparable SrV level and is absent from the comparison.
- **Rationale:** The measured spread is the argument. The intra-Gemeinde scope is uniformly
  excellent (per-Kreis EMD 0.010-0.031, aggregate 0.011) while the inter-Gemeinde scope is
  uniformly poor (per-Kreis 0.047-0.162, aggregate 0.085): the mismatch is in WHICH Gemeinde a
  commuter is assigned to, not in how far they travel inside their own Gemeinde. That is exactly
  the signature the intra/inter split was introduced to separate, and it attributes the work gap
  to the OD/friction layer (#359) and closes the per-person-distance layer (#360). For education
  the same reading applies at level granularity: `grundschule` is the one level where the
  aggregate is decisively off (0.116 vs a 0.021 floor, on 571 SrV persons), while `sekundar_1`
  and `upper_secondary` sit at or below the threshold in aggregate and `kindergarten`'s
  per-Kreis gaps are all on samples too small to be decisive.
  **This is a measurement against a reference, not a validation of the model.** It says how far
  the realised distributions sit from SrV 2023 under a pre-registered metric; it does not
  establish that the model reproduces observed travel behaviour, and no convergence or
  equilibrium claim is involved. Two explicit assumptions bound it: (1) Wolfsburg (03103) is not
  surveyed by SrV 2023 and was compared against the RegioStaR-7 type-72 pool (`source =
  proxy_rs7_72`) -- an **ASSUMPTION**, not an observation, and it is one of the decisive cells for
  layer 1 (though not the only one: the aggregate and four surveyed Kreise gap independently);
  (2) the model distance is euclidean x 1.3, not a routed distance, while the SrV reference is
  GIS-routed (ADR-0102).
  One methodological caveat on the run itself: three location stages
  (`synthesis.population.spatial.primary.candidates`,
  `braunschweig.synthesis.locations.education_gravity`,
  `braunschweig.locations.synthesis.replacement_education_gravity`) were re-executed rather than
  loaded, so the measured destinations are a same-seed re-draw on the cached i329 inputs under
  i329's own commit b7eed9a5, not the pickles the i329 run wrote. The run manifest records why
  and what that changed in the cache.
- **Consequences:**
  - #359 (layer 1, per-Kreis inter-Gemeinde commute friction) is **scheduled**; a plan is written
    before any code, per phase.
  - #360 (layer 2, SrV-conditioned per-person commute distances) is **closed with this ADR as the
    record** -- the layer would calibrate something that already matches.
  - #279 (education distances) is **narrowed to `grundschule`**; `kindergarten`, `sekundar_1` and
    `upper_secondary` are closed on this evidence, and `university` is parked as not decidable on
    SrV until a reference with adequate n exists.
  - The pre-registered rule's aggregate exemption from the 200-person floor is a known weakness:
    it let a 142-person reference produce a "build" verdict. Any future revision of
    `braunschweig.calibration.decision` should require a minimum n for the aggregate too; this is
    recorded, not silently changed, because the rule was pre-registered for this measurement.
  - `docs/registry/features/srv_primary_distance_validation.yml` moves to
    `validation.state: measured_vs_reference` and cites the run manifest.
  - The baseline is the comparison point for every later run of the same stage; re-running it on a
    future 100% population is the only way to claim any of these gaps has closed.
- **Evidence:** the run manifest `docs/runs/srv-primary-distance-baseline-2026-09-03.yml`; the
  committed measurement directory
  `eqasim-data/data/braunschweig/calibration/srv_distance_baseline_2026-09-03/`
  (`commute_by_kreis.csv`, `education_by_kreis_level.csv`, `commute_quantiles_model.csv`,
  `decisions.json`, `provenance.json`, `summary.md`, the four band plots and `run_log.txt`) --
  every number quoted above is read from `summary.md` / `decisions.json` / `provenance.json` in
  that directory; ADR-0102 (the reference and its limitations); the measured run
  `docs/runs/100pct-allfeat-i329-2026-08-24.yml`; issues #357, #358.
