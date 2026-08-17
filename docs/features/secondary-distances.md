# Purpose-resolved secondary activity distances (Tier 1 + Tier 2)


Secondary activity trip-distance distributions are refined by sourcing the
desired leg distance per **eqasim secondary purpose** (shop / leisure / other)
instead of per mode only, and — for shopping — by distinguishing **daily-needs
vs non-daily** trips both in distance sampling and in which building type they
are placed at. The eqasim activity taxonomy is **unchanged**: the pipeline output
purpose stays `shop` / `leisure` / `other`; the resolution is internal to the
distance sampler and the location placement.

**Root cause.** `_sample_leg_distance` in
`braunschweig/synthesis/locations/secondary_chainsolvers/` (a single-file stage
module at the time; a stage package with the same synpp module path since the
#266 split — the function now lives in its `distance_sampling` submodule)
previously drew the
desired distance from `distance_distributions[mode][travel_time_band]` — purpose
was ignored (except a leisure scaling factor). So a shop-by-car and a
leisure-by-car leg drew the **same** distribution, diluting shop distances by the
longer leisure tail. MiD 2023 W_GEW-weighted mean distances by coarse purpose
confirm a ~3× shop subtype range (daily 3.9 km vs non-daily 8.6 km) and a ~5×
leisure subtype range (dog-walk 3.9 km vs visit-friends 21.3 km). The OFF
baseline measures shop EMD 0.053, leisure EMD 0.064, other EMD 0.018 against MiD
W12 — all below the 0.08 quality threshold, so this is a **realism refinement**,
not a broken model.

**Tier 1 — per-(purpose x mode) distributions (`secondary_distance_by_purpose`).** 
`braunschweig/popsim/distance_distributions.py` (`run`) builds CDFs grouped by
(secondary purpose x mode x travel-time band) instead of (mode x band),
W_GEW-weighted, using `wegkm_imp / DETOUR_FACTOR` (1.3 — documented ASSUMPTION;
see below) for the euclidean conversion. The output structure gains a purpose
layer: `distributions[purpose][mode]{bounds, distributions:[{values,cdf}]}`.
`_sample_leg_distance` indexes `[purpose][mode][band]` when the purpose layer is
present; absent it falls back to the legacy `[mode][band]` path (OFF
byte-identical). Sparse-cell fallback: if a (purpose, mode) cell has fewer than
`secondary_distance_min_obs` legs, the pooled (any-purpose, mode) distribution is
used and the rate is **logged** — never silent. This is a popsim_mid-only
enhancement; the default ENTD stage is untouched.

**Tier 2 — daily / non-daily shopping split (`secondary_shop_daily_split`).** 
Tier 1 already incorporates the shop aggregate distribution (~80% daily mix).
Tier 2 additionally makes the **joint (distance, building type)** realistic.

- **Subtype imputation.** Each synthetic shop leg is imputed a subtype (`daily` /
  `non_daily`) by a seeded draw from `P(daily | mode, travel-time band)` learned
  from the labelled CATI/CAWI MiD legs (MiD W_ZWD 501 = daily; 502/503/504/505 =
  non-daily; PAPI/children sentinels 2202/4402/... excluded from estimation). The
  conditional model is *estimated* on the 60% of shop legs that carry W_ZWD, then
  *imputed onto 100%* of synthetic shop legs from their observed covariates — so a
  PAPI-donor leg still receives a subtype draw, nothing is dropped. The labelled
  fraction, per-cell counts, and any covariate cell that fell back to the marginal
  share are **logged** (no silent fallback). Implemented in
  `braunschweig/popsim/shop_subtype.py`. The seeded RNG uses `random_seed` plus
  an offset.
- **Distance.** Tier 1's shop distribution is further split into `shop_daily` /
  `shop_non_daily` (W_ZWD 501 vs 502-505), and `_sample_leg_distance` uses the
  subtype key.
- **Building placement.** The chainsolver's `_build_locations_df` maps internal
  activity `shop_daily` to the `potential_retail_daily` column and `shop_non_daily`
  to `potential_retail_non_daily`, splitting the `pot_shop` sum that was previously
  undifferentiated. After solving, both internal names map back to eqasim purpose
  `shop` — the location output schema (`[person_id, commune_id, location_id,
  geometry]`) is unchanged. The carla solver accepts the internal activity names
  directly (verified by a smoke test in `tests/test_secondary_chainsolvers.py`).

**Tier 2 — leisure (4-group) and other/errand subtype split (`secondary_leisure_subtype_split`,
`secondary_other_subtype_split`, issue [#127](https://github.com/TUBS-IVS/eqasim-bs/issues/127)).**
Generalises the shop daily/non-daily mechanism (distance + placement, conditioned on a
subtype) from a single binary purpose to two multinomial purposes, using the same
estimate-on-labelled / impute-onto-100% / log-the-rate pattern.

- **Motivation.** The single `leisure` distance layer blurred a real three-tiered
  structure: a 2026-07-09 W_GEW-weighted measurement on raw MiD Wege (`wegkm_imp`
  clipped at 200 km) found a ~25x spread inside `leisure` (dog-walk-class legs ~4 km
  vs a 45-100 km excursion tail) and a ~3x spread inside the errand share of `other`
  (W_ZWECK=5, 5-9 km vs 11-16 km).
- **Taxonomy (measured donor means; grouping criterion is the measured W_ZWD distance
  clustering, NOT a codeplan-confirmed semantic label -- the MiD 2023 codeplan xlsx
  was not available during implementation, so every per-code semantic label carries a
  `label to verify (codeplan)` marker in `braunschweig/popsim/purpose_subtype.py`; do
  not cite these as "visit friends" / "dog walk" etc. without resolving that marker
  first).** Two boundary codes are explicitly provisional and documented in the module
  docstring with their reassignment rule: W_ZWD 799 (`leisure_activity`; would move to
  the sentinel set if the codeplan shows it is a no-assignment code) and W_ZWD 601
  (`other_errand_short`; would move to `other_errand_long`).

  | leisure group | W_ZWD codes | measured mean | placement |
  |---|---|---|---|
  | `leisure_local` | 706, 710, 711, 713, 716 | ~4-7 km | `potential_leisure` |
  | `leisure_visit` | 701 | 19.1 km | `potential_visit` (residential, NEW) |
  | `leisure_activity` | 702, 703, 704, 707, 720, 721, 799 | ~10-18 km | `potential_leisure` |
  | `leisure_excursion` | 708, 709, 722 | 45-100 km | `potential_leisure` (boundary-clip share logged) |

  | other group | definition | measured mean | placement |
  |---|---|---|---|
  | `other_errand_short` | W_ZWECK=5 & W_ZWD in {601, 602} | ~5-9 km | smart-other potential (unchanged, PR #77) |
  | `other_errand_long` | W_ZWECK=5 & W_ZWD in {603, 604, 605, 699} | ~11-16 km | smart-other potential (unchanged) |
  | `other_escort` | W_ZWECK=6 (Bringen/Holen; no W_ZWD detail exists) | ~4.5-8.5 km | smart-other potential (unchanged) |
  | `other_rest` | remaining `other` W_ZWECK values | not separately estimated | smart-other potential (unchanged) |

  Sentinels (2202/4402 PAPI/child, 599/999, and cross-purpose stray codes) are
  excluded from estimation and imputed like the unlabelled leisure/other legs, mirroring
  the shop pattern. `other_escort` needs no W_ZWD label because `W_ZWECK` itself is
  always present; the errand short/long split is estimated only within `other_errand`
  legs. Placement of all four `other` groups is deliberately left on the existing
  smart-other potential (PR #77, [docs/features/smart-other-potential](../features) is
  covered by the general potentials doc) -- no placement change for `other` in this
  wave (avoids a double intervention).
- **Generic estimator (`braunschweig/popsim/purpose_subtype.py`).** Generalises the
  shop pattern (previously `shop_subtype.py`-only) to multinomial groups:
  `estimate_group_probabilities` learns `P(group | mode, travel-time band)` W_GEW-weighted
  on labelled legs (`min_obs=30` per cell, marginal fallback), `impute_groups` draws a
  group per synthetic leg with a seeded RNG, `code_coverage_guard` raises if a labelled
  code is not mapped to any group or sentinel (no silent NaN bucket). `shop_subtype.py`
  is untouched (byte-identical shop goldens); migrating it into the generic module is
  explicitly out of scope for this wave (follow-up, avoids golden churn).
- **Distance layer (`braunschweig/popsim/distance_distributions.py`).** Two new steps
  (Step 8 leisure, Step 9 other) mirror the existing shop-split step: build CDFs per
  group x mode x travel-time band from the raw `W_ZWECK`/`W_ZWD` columns (the raw
  `W_ZWECK` code is now kept through the optional-column selection specifically because
  `other_escort` (W_ZWECK=6) and `other_errand` (W_ZWECK=5) both map to the same
  `following_purpose == "other"` and would otherwise be indistinguishable), warn and
  skip if the required column is absent, keep the aggregate `leisure` / `other` key
  either way. Both flags require `secondary_distance_by_purpose=True`.
- **Imputation + leg rewrite (`secondary_chainsolvers/deciders.py` + `plans.py`).** `_build_leisure_subtype_decider`
  / `_build_other_subtype_decider` estimate once at decider-build time (logging the
  labelled-share and fallback rate there, not per leg, to avoid log spam at population
  scale) and impute a group onto every synthetic leg via dedicated seeded RNG streams:
  `LEISURE_SUBTYPE_SEED_OFFSET = 90212`, `OTHER_SUBTYPE_SEED_OFFSET = 90213` (next to
  the existing `SHOP_SUBTYPE_SEED_OFFSET = 90211`), each fully independent so any
  subset of {shop, leisure, other} splits being ON/OFF leaves the others' draws and the
  OFF path byte-identical. `other_rest` legs are deliberately left at their default
  `to_act_type == "other"` placement/distance behaviour (no group-specific treatment).
- **`pot_visit` residential placement (`leisure_visit_building_potential`, requires
  `secondary_leisure_subtype_split`).** `leisure_visit` legs (measured mean 19.1 km,
  MiD W_ZWD=701) are placed at residential buildings instead of the generic leisure
  potential, reusing `braunschweig.data.buildings` -- the SAME ALKIS-derived,
  area-weighted residential frame `synthesis/locations/home_cell.py` already consumes
  for home placement (`weight = area_m2` as the dwelling-capacity proxy; no new data
  source). `append_residential_visit_candidates` appends one candidate row per
  residential building carrying `offers_visit=True` / `pot_visit=weight`; logs the
  before/after candidate-frame row count and warns if growth exceeds a
  `VISIT_CANDIDATE_WARN_FACTOR = 3.0`. `_build_locations_df` fails fast (`ValueError`,
  naming the flag) if the flag is ON without the leisure split, or if `pot_visit` is
  absent from the candidate frame -- no silent fallback to the generic leisure pot.
  The other three leisure groups are unaffected. `configure()` declares the
  `braunschweig.data.buildings` stage only when this flag is ON.
- **Boundary-clip transparency (`leisure_excursion`).** Because the 45-100 km
  excursion tail can exceed the region's candidate radius, `secondary_chainsolvers.execute()`
  computes, for every `leisure_excursion` leg, the per-anchor maximum reachable distance
  to any `pot_leisure`-offering candidate (buildings plus the external Gemeinde
  centroids used for long-distance secondary trips) and logs the share of excursion
  legs whose desired distance exceeds that ceiling (i.e. clip to the region edge) --
  always printed, including the 0/0 case, with a `WARNING:` prefix at or above 50%
  clipped. This is expected behaviour for a genuinely long-tail purpose, not a bug, but
  must stay visible per CLAUDE.md's no-silent-fallback rule.

**Config keys (all default false / null so OFF = byte-identical to pre-feature).**

| Key | Default | Effect |
|---|---|---|
| `secondary_distance_by_purpose` | `false` | Tier 1 purpose x mode distributions (popsim_mid) |
| `secondary_shop_daily_split` | `false` | Tier 2 shop daily/non-daily split + placement |
| `secondary_shop_daily_share` | `null` | Pin the daily share; `null` = derive from MiD W_GEW |
| `secondary_leisure_subtype_split` | `false` | Tier 2 leisure 4-group split (distance only) |
| `secondary_other_subtype_split` | `false` | Tier 2 other/errand 4-group split (distance only) |
| `leisure_visit_building_potential` | `false` | Places `leisure_visit` legs on residential `pot_visit`; requires `secondary_leisure_subtype_split` |
| `secondary_distance_min_obs` | `30` | Sparse-cell fallback threshold (legs per cell) |

All five feature flags (`secondary_shop_daily_split`, `secondary_leisure_subtype_split`,
`secondary_other_subtype_split`, `leisure_visit_building_potential`, alongside the Tier 1
`secondary_distance_by_purpose`) are set to `true` once in the composed all-features
base `configs/base_bs.yml` (config-composition cleanup, #230), applying to every scale
overlay (`configs/overlays/{test_1pct,test_25pct,test_100pct}.yml`). Prior to #230 this
was set individually in the three now-removed server all-features popsim_mid run
configs (`config_server_braunschweig_{1pct,25pct,100pct}_allfeat_popsim.yml`). All other
configs leave them `false`.

**Validation.** `scripts/validate_secondary_distances.py` compares realised
secondary trip band shares (detour-adjusted via `metrics.apply_detour`) to MiD W12
(Einkauf / Freizeit / Erledigung) using `braunschweig.calibration.targets.load_w12_band_shares`.
Outputs land under `eqasim-data/data/braunschweig/calibration/secondary/`. The
W12 EMD before Tier 1 (OFF baseline: shop 0.053, leisure 0.064, other 0.018) is
the honest before-state; the after-state requires a full 25% ON synpp run (the
`cache_bs_25pct_allfeat` re-run is the next step and has not been completed at the
time of this commit). The Tier 2 placement check (daily trips at
`retail_daily` buildings vs non-daily at `retail_non_daily`, plus mean distances)
is a **structural realism** check only: MiD W12 is a distance distribution, not a
placement target, so Tier 2 has **no committed W12 target** — do not interpret a
convergence of placement ratios as a validated fit (see CLAUDE.md
"convergence != validation").

For the leisure/other subtype split, `braunschweig/calibration/secondary_measurement.py`
adds `per_group_distance_summary` (per-group leg count + weighted mean distance) and
`placement_share_at_positive_potential` (share of legs placed at a positive-potential
candidate, e.g. `leisure_visit` at `pot_visit`), wired into
`scripts/validate_secondary_distances.py::build_subtype_report()`. `SUBTYPE_DONOR_MEAN_KM_RANGE`
holds the measured donor mean/range values from the taxonomy table above, explicitly
labelled in the code as "NOT a validated external target — in-sample sanity check only"
per CLAUDE.md's "no invented reference values" rule. **Known gap:** the internal
per-leg subtype label (`leisure_local`/.../`other_escort`) is discarded (mapped back to
the aggregate eqasim purpose `leisure`/`other`) before `secondary_chainsolvers` returns
`df_locations`; the cached `activities`/`locations` synpp stages therefore carry no
subtype column today, so `build_subtype_report()` logs an explicit "not available in
this cache" line rather than fabricating a number. Persisting the subtype label onto
the cached output is a follow-up (tracked qualitatively here, not yet filed as a
separate issue).

**Verification status (2026-07-09).**

- OFF path (all three new flags `False`, code default): byte-identical to
  pre-feature output, confirmed by dedicated golden tests at every touched layer
  (`purpose_subtype`, `distance_distributions`, `secondary_chainsolvers`) plus the
  full existing regression suite.
- Unit-tested: generic multinomial estimation + weighted min_obs fallback,
  deterministic imputation, code-coverage guard (raises on an unmapped labelled
  code), distance-layer wiring, `pot_visit` candidate appending + fail-fast guards,
  and the boundary-clip/placement-share/per-group-mean helpers — all against
  synthetic frames (project convention: no real MiD/ALKIS data in unit tests).
- **Server 1% smoke flag-ON: OPEN, deferred.** The plan's Step 2 (an isolated-worktree
  1% e2e run on `felix` verifying the new log lines — labelled shares, per-cell
  fallback rates, boundary-clip share — the W12 aggregate EMD not regressing vs the
  OFF baselines shop 0.053 / leisure 0.064 / other 0.018, and per-group realised
  means plausible vs the taxonomy table) has **not** been run: the server is
  currently occupied by the user's production run. This is an explicit follow-up
  before relying on this feature in a larger run, not a silent gap — the config
  flags are enabled in the run configs ahead of that smoke per project convention
  ("new features default ON in run configs"), so the smoke is the next required
  step, not a blocker to committing this wiring.
- Per-group realised means and the `leisure_visit` placement share are **not yet
  observable** on any cache (see "Known gap" above) — the server smoke can only
  confirm the boundary-clip and fallback/labelled-share log lines until that gap is
  closed.

**Assumptions and limits.**

- `DETOUR_FACTOR = 1.3` (constant, **ASSUMPTION**): the model output is euclidean
  distance, the MiD W12 reference is routed. Short trips empirically have higher
  circuity; a distance-dependent `f(d)` is Tier 3-C backlog (see below).
- The CATI/CAWI-only detail (60% coverage) is sufficient for estimation; the
  unlabelled 40% are imputed from the conditional model on observed covariates —
  this is statistical imputation, not an invented per-leg label.
- popsim_mid only; the default ENTD distance stage is untouched.

**Tier 3 — backlog (not built; separate specs if warranted).**

- *(A) Residual scorer tuning.* Tier 1+2 fix the desired distance; the combined
  scorer's `pot_weight` pull toward large buildings may add a residual. Tune
  `secondary_dist_dev_weight` / `secondary_scorer_pot_weight` only after the 25%
  ON run confirms a residual attributable to the scorer.
- *(C) Distance-dependent detour `f(d)`.* Circuity decays with distance (Ballou
  et al.; Giacomin & Levinson 2015, *Road network circuity in metropolitan areas*).
  Derive `f(d)` empirically from the ZGB MATSim network; this is cross-cutting
  (commute, education, secondary all use `DETOUR_FACTOR`) and belongs in its own
  spec with re-validation of all three.

Tests: `tests/test_distance_distributions_by_purpose.py`,
`tests/test_sample_leg_distance_purpose.py`,
`tests/test_shop_subtype.py`,
`tests/test_calibration_targets.py` (W12 additions),
`tests/test_secondary_chainsolvers.py` (Tier 2 + carla smoke).

**Leisure/other subtype split tests (#127):** `tests/test_purpose_subtype.py` (generic
estimator + imputation + code-coverage guard, both taxonomies), 
`tests/test_distance_distributions_subtypes.py` (leisure/other distance-layer steps,
OFF-path key-set identity), `tests/test_secondary_chainsolvers_subtypes.py`
(imputation deciders, leg rewrite, `pot_visit` candidate appending + placement, carla
smoke for `leisure_excursion`/`other_escort`), `tests/test_secondary_subtype_validation.py`
(per-group distance summary, placement share, boundary-clip share, `build_subtype_report`
honest-skip vs real-computation branches).

### External secondary candidates (long-distance trips)

`secondary_external_candidates` (default true; on only where `cordon_enabled` is true)
appends German Gemeinde centroids OUTSIDE ZGB (vg250, population-weighted,
`braunschweig.data.external_secondary_points`, `commune_id = "EXT<gem_ags8>"`) to
the secondary candidate set, so carla matches MiD long desired distances (~6% of
leisure / ~3% of other exceed the ~50 km area, measured on the 1% all-features
cache) to a far external centroid instead of truncating to the area edge. MATSim
routability is handled by eqasim's `RunScenarioCutter` (cordon_enabled), which
converts the boundary-crossing secondary trip into a fixed "outside" activity —
the same mechanism used for work out-commuters (`braunschweig.data.external_workplaces`).
A warning is logged if the flag is on without cordon. Direction is a distance-only
proxy (no secondary OD data; ASSUMPTION, as in external_workplaces); the realised
MATSim network distance ends at the cordon while the synthesis distance is the full
value. OFF path byte-identical.

Tests: `tests/test_external_secondary_points.py`, `tests/test_secondary_chainsolvers.py`,
`tests/test_secondary_external_wiring.py`.
