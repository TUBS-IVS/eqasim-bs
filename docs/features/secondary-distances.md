# Purpose-resolved secondary activity distances (Tier 1 + Tier 2)


Secondary activity trip-distance distributions are refined by sourcing the
desired leg distance per **eqasim secondary purpose** (shop / leisure / other)
instead of per mode only, and — for shopping — by distinguishing **daily-needs
vs non-daily** trips both in distance sampling and in which building type they
are placed at. The eqasim activity taxonomy is **unchanged**: the pipeline output
purpose stays `shop` / `leisure` / `other`; the resolution is internal to the
distance sampler and the location placement.

**Root cause.** `_sample_leg_distance` in
`braunschweig/synthesis/locations/secondary_chainsolvers.py` previously drew the
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

**Config keys (all default false / null so OFF = byte-identical to pre-feature).**

| Key | Default | Effect |
|---|---|---|
| `secondary_distance_by_purpose` | `false` | Tier 1 purpose x mode distributions (popsim_mid) |
| `secondary_shop_daily_split` | `false` | Tier 2 daily/non-daily split + placement |
| `secondary_shop_daily_share` | `null` | Pin the daily share; `null` = derive from MiD W_GEW |
| `secondary_distance_min_obs` | `30` | Sparse-cell fallback threshold (legs per cell) |

Both flags are set to `true` in the two server all-features popsim_mid run configs
(`config_server_braunschweig_1pct_allfeat_popsim.yml` and
`config_server_braunschweig_25pct_allfeat_popsim.yml`). All other configs leave
them `false`.

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
