# ADR-0102 · 2026-09-03 · SrV 2023 as the regional distance reference for the primary activities
- **Status:** active
- **Context:** The realised home->work / home->education distance distributions were only ever
  checked against MiD 2023 Pendeldistanz tables: P13 (Tabelle A P13, page 77 -- the REGIONAL
  "Grossraum Braunschweig" (infas 7555) evaluation of commute distance to the usual workplace
  (Pendeldistanz), including non-daily commuters, n_unweighted 1,583, RegioStaR-7 type 71 absent
  from this region) and P38.2 (Kreis-level commute-distance bands and means). Both already carry
  long-distance mass that SrV structurally cannot see: P13's Gesamt row has ~2% of persons at
  >= 100 km (`d_100p`), and P38.2's Gesamt row has ~13% (`d_100_200` 6% + `d_200_300` 4% +
  `d_300km_plus` 3%), while SrV's realised Tuesday-Thursday day trips carry NO home-based trip
  >= 100 km at all in this delivery -- a band a regional day-trip survey structurally cannot see
  and a model calibrated against SrV should not be asked to hit. The 2026-06-25 friction
  measurement (ADR-0041) predates the VerBindungen inner anchor (#193) and the TAZ work, and the
  2026-08-20 100% run (`docs/runs/100pct-allfeat-i240-2026-08-20.yml`) recorded no distance
  validation at all. The regional SrV 2023 "Braunschweig und Regionalverband Grossraum
  Braunschweig" scientific-use microdata is available locally and its Zensus-expansion weighting
  is already the project standard (ADR-0055). Issue #358 (parent #357) asked for a committed,
  Kreis-level, GIS-routed reference so the analysis stages
  `braunschweig.analysis.reference.srv.commute_distance` and
  `braunschweig.analysis.synthesis.commute_distance_by_kreis` compare every production run
  against a traceable, non-invented target.
- **Decision:** Use SrV 2023 as THE distance-distribution reference for work (V_ZWECK 1) and
  education (V_ZWECK 3, 4, 5, 6) per home Kreis, with the following definitions, all implemented
  in `scripts/extract_srv_primary_distance_targets.py` and pinned by
  `tests/test_srv_distance_targets_pins.py`:
  - Observation unit = person, first home->purpose trip (`V_START_LAGE == 1`), else first
    purpose->home trip (`V_ZIEL_LAGE == 1`) -- the eqasim `data.hts.commute_distance`
    definition; business trips (V_ZWECK 2) and "andere Bildungseinrichtung" (V_ZWECK 7) are
    excluded; reporting days are Tuesday-Thursday only.
  - Distance = `GIS_LAENGE` (GIS-routed km) where `GIS_LAENGE_GUELTIG > 0`. A GIS-invalid trip
    falls back to the other direction (R5); if both directions are GIS-invalid, or a negative
    weight or an over-cap distance (> 300 km) lands on the selected trip, the person is excluded
    rather than substituted (R6; the missingness reasoning for both cases is in ASSUMPTIONS 2
    and 3 below). Pool-level negative-weight/over-cap counts are kept as diagnostics only.
  - Weight = `GEWICHT_W_ZENSUS` (expansion to Zensus 2022); `GEWICHT_W_WERKTAG` is undefined in
    this delivery and is not used.
  - Kreis = first 5 digits of the household AGS. Work bands (routed km):
    `[0, 5, 10, 20, 30, 50, 100, inf]`; education bands: `[0, 1, 2, 5, 10, 20, inf]`.
  - Targets are shrunk `n/(n+k)`, `k = 100` persons, hierarchy Kreis -> weight-modal RegioStaR-7
    pool (itself shrunk toward the ZGB total) -> ZGB; the quantile table is shrunk quantile-wise
    (monotone), reported in euclidean-equivalent km via `routed / 1.3`.
  - Education levels follow the model's own age banding, not the SrV instrument codes directly
    (R4): Kita (V_ZWECK 3) counts only at age 0-6; Grundschule (V_ZWECK 4) only at age 5-10
    (model age band +/- 1 year); sekundar_1 = V_ZWECK 5 at age 10-15; upper_secondary = V_ZWECK 5
    or 6 at age 16-19 (oberstufe and BBS pooled, because the model's education output carries no
    level and derives one from age alone); university = V_ZWECK 6 at age 20+. Persons outside
    every band are excluded from the comparable levels and counted: 65 of the 2,503 selected
    education persons (2.6%), i.e. the education table's `n_persons_selected=2503` header value
    minus the five comparable-level ZGB rows (kindergarten 647 + grundschule 571 + sekundar_1
    718 + upper_secondary 360 + university 142 = 2,438).
  - The band EMD reported as `emd_noise_95_*` is normalised to `[0, 1]` exactly like
    `braunschweig.calibration.metrics.emd_on_bands` (re-implemented locally in the extractor to
    avoid a pipeline import), so the SrV noise floor, the comparison stage and the project's
    0.08 EMD threshold share one unit.
  - The commute table additionally carries exact per-scope person counts `n_persons_inter` /
    `n_persons_intra` (intra = start AGS == destination AGS) so the pre-registered >= 200-persons
    gate (`braunschweig.calibration.decision`) applies per scope, not only to the "all" total.
  - Tables are committed as aggregates only, never the raw microdata:
    `eqasim-data/data/braunschweig/srv/srv2023_{commute_distance_by_kreis,
    education_distance_by_kreis_level, commute_distance_quantiles_by_kreis}.csv`.
- **ASSUMPTIONS:**
  1. Wolfsburg (03103, not surveyed) is represented by the RegioStaR-7 type-72 pool
     (Braunschweig + Salzgitter, Zensus-weighted), `source = proxy_rs7_72`; per R11 that row
     carries the pool's raw shares/quantiles in BOTH the raw and shrunk columns (a pool is not
     shrunk further), because there is no surveyed Wolfsburg cell to shrink toward.
  2. GIS-invalid trips (16.9% of 9,730 work candidate trips, 15.7% of 5,449 education candidate
     trips) fall back to the person's other direction; a person is excluded only when BOTH
     directions are GIS-invalid (R6). This is the SINGLE home of the GIS-validity bias-check
     numbers (do not restate them elsewhere without a link back here); they are reproducible
     via `scripts/extract_srv_primary_distance_targets.py --bias-check`
     (`braunschweig.calibration.srv_distance_targets.gis_validity_bias_check`), which computes
     them from the committed selection logic (home-based work candidate trips, both
     directions) rather than from a one-off, uncommitted script. Ruling R27 (controller,
     whole-branch review follow-up) recomputed the check excluding SrV missing-data sentinel
     codes on `V_LAENGE` (e.g. -5 "weiss nicht", -10 "unplausibel"; 426 of the 1,643 GIS-invalid
     trips, 25.9%, carry no usable self-reported length at all) from every median/mean:
     self-reported median 13.00 km (GIS-invalid) versus 12.00 km (GIS-valid, n=8,087), mean
     26.1 km versus 16.5 km, GIS/self-reported ratio 0.994 (both directions and outbound-only
     give the same medians/ratio). ASSUMPTION: missing-at-random with respect to distance (R13)
     holds at the CENTRE of the distribution (median 13 vs 12 km, a modest difference) but NOT
     in the tail (mean 26.1 vs 16.5 km; 25.9% of GIS-invalid trips report no length at all):
     GIS-invalid trips carry a heavier long-distance tail than GIS-valid ones, so excluding them
     may understate the long-distance mass of the SrV targets -- consistent with the empty
     `100_plus` band below. Layer 1 (#359) must consider a self-reported-length fallback or an
     explicit tail correction before pinning friction factors, rather than treating the
     `100_plus` band's emptiness as settled fact. A direct consequence already known independent
     of this refinement: the commute band `100_plus` is 0.0 in every row (no GIS-valid
     home-based trip >= 100 km exists in this delivery), so that band cannot be calibrated from
     SrV at all; the OD anchors (BA Pendleratlas, VerBindungen) own that mass instead -- the same
     mass that MiD's Pendeldistanz tables show is non-trivial (P13 ~2%, P38.2 ~13% at >= 100 km).
  3. A negative weight or an over-cap distance (> 300 km) on the SELECTED trip excludes the
     person rather than falling back to the other direction (R6) -- a missingness choice, not a
     substitution, because a fallback direction with those same defects would be equally
     unusable. In this delivery both counts are zero for both purposes
     (`n_excluded_weight_negative=0, n_excluded_over_cap=0` in the committed CSV headers), so the
     rule has not yet been exercised on the ZGB sample.
  4. Per-Kreis rows extrapolate a stratified PSU design over roughly 44 selected municipalities
     to the full Kreis.
  5. Ruling R26 (whole-branch review): the pre-registered EMD threshold `0.08`
     (`braunschweig.calibration.decision.DEFAULT_EMD_THRESHOLD`) was derived for the WORK
     commute band grid (`WORK_BAND_EDGES_KM`, 7 bands, `[0, 5, 10, 20, 30, 50, 100, inf]`) via
     the gravity-calibration-corner MiD P13 comparison (`docs/features/calibration-corner.md`).
     Applying the SAME 0.08 threshold to the EDUCATION band grid (`EDUCATION_BAND_EDGES_KM`, 6
     bands, `[0, 1, 2, 5, 10, 20, inf]`) in `braunschweig.calibration.decision.decide_layer` is
     an ASSUMPTION: no committed derivation ties 0.08 to the education grid specifically. An
     education-specific threshold derivation is tracked as a GitHub issue rather than assumed
     away.
- **Rationale:** SrV is regional, GIS-routed, and a Tuesday-Thursday realised day-trip universe --
  the apples-to-apples reference for a day-plan model, distinct from MiD's Pendeldistanz universe
  (commute distance to the usual workplace, including non-daily commuters), which carries
  long-distance mass SrV structurally does not (P13 ~2%, P38.2 ~13% of persons at >= 100 km).
  Sample sizes support this as a per-Kreis reference: work n_persons per surveyed Kreis ranges
  387 (03102, Salzgitter, minimum) to 1,272 (03101, Braunschweig, maximum), ZGB total 4,543
  persons (Wolfsburg's proxy pool: 1,659). Education n_persons per surveyed Kreis (comparable
  levels only) ranges kindergarten 37-188, grundschule 55-143, sekundar_1 70-204,
  upper_secondary 26-92, university 7-74 (ZGB totals: kindergarten 647, grundschule 571,
  sekundar_1 718, upper_secondary 360, university 142; descriptive-only oberstufe 324, bbs 36).
  The ZGB routed median work distance is 10.62 km. BBS/university cells are thin at Kreis
  granularity and are flagged by the module's own `emd_noise_95` column rather than hidden.
- **Rejected alternatives:**
  - MiD P13/T43 as the sole reference, for two reasons: (a) universe -- Pendeldistanz (commute
    distance to the usual workplace, including non-daily commuters) versus SrV's realised
    Tuesday-Thursday day trips; (b) sample -- P13's OWN per-Kreis table (`mid2023_P13.csv`,
    keyed by `ars5`) has n_unweighted ranging 126 (Wolfsburg) to 356 (Braunschweig) persons per
    Kreis, well below SrV's 387 (Salzgitter, minimum) to 1,272 (Braunschweig, maximum), and the
    REGION-wide Gesamt row (n_unweighted 1,583) is itself smaller than SrV's ZGB total of 4,543;
    the sibling `mid2023_P13_commute_distance_by_rs7.csv` is keyed by RegioStaR-7 CLASS, not
    Kreis, and is used only for the descriptive RS7-POOLED classes, not as a per-Kreis
    substitute -- an earlier version of this record wrongly described `mid2023_P13.csv` itself
    as RS7-keyed only. Note P13 is itself a REGIONAL "Grossraum Braunschweig" (infas 7555)
    evaluation, not national (RegioStaR-7 type 71 is absent from its rows, consistent with the
    region it covers); T43 (school distances by RegioStaR-7) may be national in scope, but that
    is UNVERIFIED in this repo -- the committed `mid2023_T43_school_distance_by_rs7.csv` carries
    no source header stating its geography. Comparisons against both stay alongside
    (`run_mid_validation`) to expose where the two references differ, but SrV is the calibration
    target.
  - MiD P38.2 per-Kreis means as targets: the committed table shows Salzgitter 237.6 km and
    Wolfsburg 90.4 km mean commute distances -- both outlier-driven and unusable as a target or
    even as a Wolfsburg cross-check.
  - Per-stratum targets: design-safe (matches the SrV sampling frame) but not the model's Kreis
    key, so every comparison would need an extra crosswalk with no offsetting benefit.
  - Self-reported trip lengths: rounded, with comb artefacts at round numbers; kept only for the
    GIS-invalid bias check (Assumption 2), never as the reported distance.
  - The earlier MiD-only friction plan (ADR-0041 context): its 2026-06 measurement predates the
    VerBindungen inner anchor (#193) and the TAZ work, and the 2026-08-20 100% run recorded no
    distance validation at all -- neither gives a usable regional baseline to build on.
- **Consequences:** `braunschweig.analysis.reference.srv.commute_distance` and
  `braunschweig.analysis.synthesis.commute_distance_by_kreis` compare every production run
  against these tables. The calibration layers under #357 (#359 layer 1, #360 layer 2, #279
  education) target the shrunk shares from this reference. MiD P13/T43 comparisons stay alongside
  (`run_mid_validation`) to expose where the two references differ, never to replace this one.
  The spec's optional "distance band x main mode" descriptive table and the VerBindungen
  plausibility check of the Wolfsburg proxy's inter-Gemeinde part are deferred to layer 1 (#359).
  Because the `100_plus` band is empty in every SrV row (Assumption 2), this reference cannot by
  itself calibrate long-distance commuting; that stays the OD anchors' responsibility.
- **Evidence:** the three committed tables under `eqasim-data/data/braunschweig/srv/`
  (`srv2023_commute_distance_by_kreis.csv`, `srv2023_education_distance_by_kreis_level.csv`,
  `srv2023_commute_distance_quantiles_by_kreis.csv`), generated by
  `scripts/extract_srv_primary_distance_targets.py`; the committed MiD tables
  `eqasim-data/data/braunschweig/mid/mid2023_P13.csv`,
  `mid2023_P13_commute_distance_by_rs7.csv` (source header: "MiD 2023 Grossraum Braunschweig
  (infas 7555), Tabelle A P13") and `mid2023_P38_2_commute_distance_by_kreis.csv` (source
  header: "MiD 2023 Grossraum Braunschweig (infas 7555), Tabelle A P38.2"), read directly for
  the >= 100 km figures in this record; the Data Registry record `srv2023_primary_distance_targets`;
  issue #358 (parent #357); the design spec
  `docs/superpowers/specs/2026-09-03-srv-primary-distance-calibration-design.md` (gitignored,
  per-instance design document, not a committed source -- cited for context only, not as the
  origin of any number stated above).
