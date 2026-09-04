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
    (0.114, 0.024). The undifferentiated `all` scope also gaps and would build on its own -- three
    Kreise gap there (03153 0.104, 03158 0.100, 03157 0.097) although its ZGB aggregate is `ok`
    (0.051, floor 0.012) -- but the scope split below is what makes the verdict actionable.
  - **Layer 2 -- SrV-conditioned per-person commute distances, intra-Gemeinde (#360): DO NOT
    BUILD.** The rule reports `do not build: no gap in any Kreis with >= 200 reference persons nor
    in the aggregate`. All nine intra cells classify `ok`; the aggregate EMD is 0.011 against a
    floor of 0.008 on 1,950 SrV persons -- roughly a seventh of the 0.08 threshold -- and the two
    worst single Kreise are Wolfsburg 03103 at 0.031 (floor 0.010; the proxy Kreis, so not an
    observation) and Wolfenbuettel 03158 at 0.030 (floor 0.022), both still a factor 2.6-2.7 below
    the threshold. The model's within-Gemeinde commute-distance distribution already matches
    SrV 2023; issue #360 is to be closed with this ADR as the record rather than scheduled.
  - **Education (#279), per comparable model level:** every verdict below is classified against
    the 0.08 EMD threshold applied to the EDUCATION band grid, which ADR-0102 Assumption 5
    (ruling R26) records as an ASSUMPTION -- the threshold was derived for the WORK band grid,
    not the education one -- so a level sitting close to 0.08 (notably `upper_secondary` at
    0.074) could classify differently under an education-specific threshold.
    - `kindergarten`: **DO NOT BUILD** -- aggregate EMD 0.067 (floor 0.020, n_ref 647) classifies
      `ok`; four Kreise gap (03102, 03153, 03157, 03158) but each rests on only 53-94 SrV persons,
      far below the 200-person floor, so no cell is decisive.
    - `grundschule`: **BUILD** -- the decisive cell is the ZGB aggregate, EMD 0.116 (floor 0.021,
      n_ref 571); six of the eight Kreise gap as well (03101, 03151, 03153, 03154, 03157, 03158;
      03102 and the proxy Kreis 03103 are `ok`), worst Wolfenbuettel 03158 (0.225, floor 0.058)
      and Helmstedt 03154 (0.211, 0.049), though none of those Kreise reaches 200 SrV persons.
    - `sekundar_1`: **DO NOT BUILD** -- aggregate EMD 0.025 (floor 0.023, n_ref 718) classifies
      `ok`; only 03153 (0.086, 70 SrV persons) and 03158 (0.093, 71) gap, neither decisive.
    - `upper_secondary`: **DO NOT BUILD** -- aggregate EMD 0.074 (floor 0.033, n_ref 360)
      classifies `ok`; five Kreise gap (03102, 03153, 03154, 03157, 03158) but each rests on only
      26-57 SrV persons. This level carries the model's vocational (`bbs`) pupils as well: model
      levels are derived from age alone, so `upper_secondary` at ZGB level pools 12,694 `oberstufe`
      and 5,427 `bbs` pupils into its 18,121 modelled persons, and the SrV side pools its own
      descriptive `oberstufe` and `bbs` rows into the same comparable level.
    - `university`: the rule AS PRE-REGISTERED on 2026-09-03 reported **BUILD** on the aggregate
      alone (EMD 0.202, floor 0.083) -- **superseded by the amendment below** (section
      "Amendment 2026-09-04"), which re-derives this verdict as UNDECIDABLE on the same
      byte-identical cells.
      and every per-Kreis cell either gaps (six -- 03101, 03102, 03103, 03151, 03154, 03157, up to
      0.482 for the proxy Kreis Wolfsburg 03103) or is `within_noise` (03153, 03158, whose noise
      floors of 0.279 and 0.220 exceed their EMDs). **This decision is NOT acted on as a
      calibration mandate**: the whole university reference is 142 SrV persons at ZGB level and
      7-84 per Kreis, so NO
      university cell reaches the 200-person floor and the "build" verdict rests entirely on the
      aggregate, which the pre-registered rule exempts from that floor. On this evidence the
      level is *not decidable on SrV*; the university slopes stay on the T43/Mikrozensus basis
      and the discrepancy is recorded as an open question for a reference with adequate n. The
      rule is left as pre-registered -- it is not retro-fitted to this outcome -- but its
      aggregate exemption is recorded here as a limitation (see Consequences).
    - The model's `bbs` cohort is NOT excluded from the comparison: it is measured inside
      `upper_secondary` (see that entry). What has no comparable counterpart is the SrV-side
      `oberstufe`/`bbs` SPLIT -- both are carried in the reference table as descriptive rows with
      `comparable = False` (118 SrV persons for `bbs`, 1,082 for `oberstufe`) and are pooled into
      the comparable `upper_secondary` level, because the model cannot tell the two apart from age.
- **Rationale:** The measured spread is the argument. The intra-Gemeinde scope is uniformly
  excellent (per-Kreis EMD 0.010-0.031, aggregate 0.011) while the inter-Gemeinde scope is
  uniformly poor (per-Kreis 0.047-0.162, aggregate 0.085): the mismatch is in WHICH Gemeinde a
  commuter is assigned to, not in how far they travel inside their own Gemeinde. That is exactly
  the signature the intra/inter split was introduced to separate, and it attributes the work gap
  to the OD/friction layer (#359) and settles the per-person-distance layer (#360) as already
  matching. For education
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
  A third caveat bounds the inter scope specifically, and it is the one that matters most for
  layer 1: 39,150 modelled work destinations fall outside every Gemeinde polygon and are counted
  as inter-Gemeinde by the stage's documented convention. That is 12.8% of all 304,900 workers but
  **24.2% of the 161,805-person inter cohort** -- roughly one in four. These are cordon-external,
  long-distance workplaces, and pooling them into the inter comparison pushes mass into the long
  bands, so the inter EMD (aggregate 0.085 against a 0.08 threshold, i.e. barely over) **may be
  overstated by an unquantified amount**. The layer-1 work (#359) must quantify this before any
  parameter is pinned -- for instance by restricting or matching the comparison to SrV trips whose
  destination AGS lies outside the ZGB, which are inter on the reference side too. Until that is
  done, the inter verdict should be read as "build and measure", not as a calibrated target. This
  caveat is to be read together with ADR-0102 Assumption 2's tail finding: both point the same
  way, that SrV may understate long distances (GIS-invalid work trips carry a heavier
  long-distance tail than GIS-valid ones), so the model's inter-Gemeinde distribution is being
  compared against a reference whose own long-distance tail is possibly short-biased.
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
  - #360 (layer 2, SrV-conditioned per-person commute distances) is **to be closed with this ADR
    as the record** -- the layer would calibrate something that already matches. Closing the issue
    itself is the maintainer's action, not this record's.
  - #279 (education distances) is **narrowed to `grundschule`**; `kindergarten`, `sekundar_1` and
    `upper_secondary` are to be closed with this ADR as the record on this evidence, and
    `university` is parked as not decidable on SrV until a reference with adequate n exists.
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

## Amendment 2026-09-04

- **Rule amendment.** `braunschweig.calibration.decision.decide_layer` gained a new parameter,
  `aggregate_requires_min_persons` (module constant `DEFAULT_AGGREGATE_REQUIRES_MIN_PERSONS =
  True`), AFTER this baseline was measured. This is disclosed here as a POST-HOC SHARPENING of the
  pre-registered rule, not a silent change: before the amendment the ZGB aggregate was exempt from
  the `min_persons` floor unconditionally, so a thin aggregate (e.g. n=142 for `university`) could
  force `build=True` on its own; with the amendment the aggregate is decisive under the SAME
  `n_reference_persons >= min_persons` floor as every other cell, and a frame with NO decisive cell
  now reports `undecidable` instead of silently resolving to "do not build". The rule was
  re-derived on the byte-identical cells of the run `srv-primary-distance-baseline-2026-09-04`
  (the measured distances did not move -- `commute_by_kreis.csv`, `education_by_kreis_level.csv`,
  `commute_quantiles_model.csv` and all four band plots are byte-identical to the 09-03 baseline;
  only the verdict computation and the added sensitivity section are new). The ONLY verdict that
  changes is `university`: BUILD -> UNDECIDABLE (`"not decidable: no cell reaches the >= 200-person
  floor (aggregate n=142) and no decisive gap exists"`), which now matches the manual override this
  record already applied above (the "university" bullet under Decision, "NOT acted on as a
  calibration mandate"). Every other verdict is unchanged: work `all` BUILD, `inter` BUILD, `intra`
  DO NOT BUILD; education `kindergarten` DO NOT BUILD, `grundschule` BUILD, `sekundar_1` DO NOT
  BUILD, `upper_secondary` DO NOT BUILD.
- **Threshold derivation (ADR-0102 Assumption 5).** The normalised band EMD
  (`braunschweig.calibration.metrics.emd_on_bands`) is the MEAN absolute CDF gap over the inner
  band edges, in share units: `sum(abs(cumsum(p) - cumsum(q))[:-1]) / (n_bands - 1)`. A value of
  0.08 therefore means an average CDF misplacement of 8 percentage points across the band
  boundaries, independent of how many bands the grid has. Applied to the finer EDUCATION grid
  (6 bands, `[0, 1, 2, 5, 10, 20, inf]` km) the same 0.08 is a STRICTER test in kilometres than on
  the coarser WORK grid (7 bands, `[0, 5, 10, 20, 30, 50, 100, inf]` km), because the same
  percentage-point misplacement is packed into narrower distance intervals -- appropriate given
  that school trips are shorter than commute trips. This remains an ASSUMPTION: no committed
  derivation ties 0.08 specifically to either grid (ADR-0102 Assumption 5 still applies; only its
  wording is refined here, see the update to that record). The measured threshold sweep
  (`sensitivity.csv` of the 09-04 baseline, thresholds 0.06/0.08/0.10) shows which verdicts sit
  close to this boundary: at 0.06, `kindergarten` (aggregate EMD 0.067) and `upper_secondary`
  (aggregate EMD 0.074) flip to BUILD; at 0.10, the sensitivity variant `all_gis_fallback` flips to
  DO NOT BUILD (its worst decisive Kreis EMD, 03153, is 0.0995, just under 0.10). No other scope,
  level or variant changes across 0.06-0.10. This is ROBUSTNESS information about how close a
  verdict sits to the boundary, not a change to the pre-registered 0.08 decision.
- **Sensitivity variants (NOT pre-registered).** `inter_zgb` (both universes restricted to
  inter-Gemeinde commutes that stay inside the 8 ZGB Kreise): zgb EMD 0.021 against a noise floor
  of 0.013 on n_ref 2,301, all nine cells `ok` -> DO NOT BUILD. The pre-registered `inter` gap
  (aggregate EMD 0.085) therefore does not survive restricting both sides to ZGB-internal
  destinations; it is carried by the polygon-external destinations, which the two sides drop at
  very different rates. BOTH readings stay open on this evidence: (a) within-ZGB destination
  choice already matches SrV, and the gap is entirely about WHICH external workplaces the model
  assigns, not about how far ZGB-internal commuters travel; (b) the model may OVER-PRODUCE
  out-of-ZGB workplaces relative to the survey -- the model excludes 24.2% of the 161,805-person
  inter cohort as polygon-external (39,150 workers) versus SrV excluding only 11.3% of its
  2,593 inter persons for a destination outside the ZGB (unweighted person counts on both sides;
  INDICATIVE of the asymmetry, not an exact like-for-like rate, since the EMDs themselves are
  computed on GEWICHT_W_ZENSUS-weighted shares). Reading (b) is possibly COMPOUNDED, not refuted,
  by SrV under-capturing long/external trips (ADR-0102 Assumption 2: GIS-invalid work trips carry
  a heavier long-distance tail than GIS-valid ones, and a one-day survey universe under-observes
  irregular long-distance commuting). `all_gis_fallback` (GIS length where valid, else the
  self-reported length): zgb EMD 0.045, `ok`, but three Kreise remain decisive gaps (03153 0.0995,
  03157 0.088, 03158 0.093) so the scope-level verdict is still BUILD -- recovering the GIS-invalid
  tail barely moves the "all" verdict. `inter_gis_fallback`: zgb EMD 0.0803, a GAP by a margin of
  0.0003 over the 0.08 threshold, with decisive gaps at 03103, 03153, 03157, 03158 and zgb ->
  BUILD; the pre-registered `inter` verdict is therefore robust to the GIS-invalid tail but not to
  the polygon-external destinations.
- **Consequence for layer 1 (#359).** The verdict BUILD stands, but the lever it points at is
  RE-TARGETED. Per-Kreis friction inside the destination Kreis is NOT indicated by this evidence
  (`inter_zgb` is `ok` everywhere); the gap sits in WHICH Kreis/point a commuter is sent to, not in
  the distance distribution once a destination Kreis is fixed. Layer 1 must therefore start by
  (1) comparing the realised out-of-ZGB commuter share per Kreis against the BA Pendleratlas
  register counts (stage `braunschweig.data.census.pendler`, consumed by
  `braunschweig.gravity.kreis_calibration._append_outbound_flows`, imported by
  `braunschweig.gravity.model`) and against SrV, and (2) characterising the distance distribution
  of the model's polygon-external ("EXT") destination points against SrV's own external trips
  (including the GIS-invalid tail, ADR-0102 Assumption 2), before deciding whether, and what, to
  calibrate.
- **Education under the amendment.** `grundschule` BUILD stands unchanged (decisive cell = the ZGB
  aggregate, EMD 0.116 vs floor 0.021, n_ref 571). `university` is UNDECIDABLE, not "BUILD" (see
  the rule-amendment bullet above). `kindergarten` and `upper_secondary` remain DO NOT BUILD at the
  pre-registered 0.08 threshold but both flip to BUILD at 0.06 (aggregate EMD 0.067 and 0.074
  respectively, both close to the threshold on samples of 647 and 360 SrV persons) -- this is
  recorded here as FRAGILE, not as a reason to change the verdict.
- **Evidence.** Both run manifests, `docs/runs/srv-primary-distance-baseline-2026-09-03.yml` and
  `docs/runs/srv-primary-distance-baseline-2026-09-04.yml`; both committed measurement directories,
  `eqasim-data/data/braunschweig/calibration/srv_distance_baseline_2026-09-03/` and
  `eqasim-data/data/braunschweig/calibration/srv_distance_baseline_2026-09-04/` (the latter
  additionally carrying `sensitivity.csv` and `sensitivity_cells.csv`); ADR-0102 (the reference
  definition, and its Assumption 2 GIS-invalid tail finding referenced above);
  `braunschweig.calibration.decision` (the amended rule); issues #357, #358, #359.
