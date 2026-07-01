# Distance-dependent detour/circuity factor (Tier 3)


**VERDICT (2026-06-25, measure-first): the constant detour factor `1.3` remains the
DEFAULT.** The distance-dependent curve was built, fitted on the 25% ZGB synthesis,
and MEASURED to be **not materially better** than the constant `1.3`
(`band_shift_impact.csv`: commute EMD vs P13 0.0878 -> 0.0849, delta ~0.003; pooled
secondary walk vs W12 0.0712 -> 0.0729, slightly worse — both far below the 0.01
materiality threshold). So the curve is **not pinned**; the `f(d)` machinery + the
committed 25% measurement (`calibration/detour/`) are retained as **opt-in
infrastructure** (`mode="curve"`) and for traceability. This mirrors the
commute-friction outcome: machinery built, measured, found not warranted — no
overfitting. The fitted curves themselves are plausible (car c_inf=1.19, c(0.5km)=1.64;
walk c_inf=1.20, c(0.5km)=1.51); they simply do not move the ZGB distributions.

**Premise.** Every calibrator in the corner converts model output (straight-line
euclidean km) to the routed axis of MiD band edges by multiplying by a detour
factor. The legacy constant `1.3` is a broad average; empirical network studies
show circuity decays with distance (Ballou et al. 2002; Giacomin & Levinson 2015,
*Road network circuity in metropolitan areas*) — short trips are more tortuous than
long ones. A fitted curve would improve the axis alignment in principle, so the
machinery was built and gated behind a **measure-first** check
(`band_shift_impact.csv`) before changing any default — which is exactly what showed
the curve to be immaterial for ZGB (see VERDICT).

**Curve form.** `c(d_km) = c_inf + a * exp(-d_km / tau)` (per network). Both
directions are exposed:
- `euclidean_to_routed(d)` = `d * c(d)` — converts model output to the routed axis.
- `routed_to_euclidean(r)` — unique inverse via `scipy.optimize.brentq`
  (converts MiD routed targets to straight-line for slope calibration).

**Module.** `braunschweig/calibration/circuity.py` — contains
`circuity_factor`, `euclidean_to_routed`, `routed_to_euclidean`, and
`load_circuity_params`. `braunschweig/calibration/metrics.py` (`apply_detour`)
delegates to this module.

`mode="constant"` (DEFAULT) reproduces the legacy `* 1.3` exactly — byte-identical
to the pre-Tier-3 pipeline. `mode="curve"` (opt-in) uses the fitted curve; it is
available infrastructure but, per the VERDICT above, not the default.

**Single source of truth for params:**
`eqasim-data/data/braunschweig/calibration/detour_circuity_params.csv` (committed,
regenerated in-place by the fit script). Car and walk rows carry `c_inf`, `a`, `tau_km`; the pt row carries
`uplift` and `base` (pt = car * uplift, see below). `load_circuity_params` validates
all fields on load (c_inf >= 1, a >= 0, tau > 0, uplift >= 1) and raises on a
missing or malformed file — fail-fast, no silent fallback.

**Networks and dispatch.**

| Context | Network | Source |
|---|---|---|
| Commute calibration | `car` | upstream of mode choice |
| Secondary validation (`scripts/validate_secondary_distances.py`) | per-leg: `car` / `pt` / `walk` from `mode_to_network` | purpose x mode layer |
| Education: kindergarten / grundschule / sekundar_1 | `walk` | MiD T43 on-foot targets |
| Education: oberstufe / bbs / hochschule | `car` | Destatis MZ 2024, motorised trips |

Note: the fit script (`scripts/calibrate_detour_circuity.py`) **excludes kindergarten
(age 0-5) from the OD sample** used to fit the walk curve. Kindergarten trips are
already represented by short-distance walk secondary legs, and their MiD T43 mean
(~1.5-2.3 km) is below the minimum-samples floor for a reliable per-level fit.
The `walk` curve is applied to kindergarten in production via the same dispatch row
above; no dedicated kindergarten OD pairs are sampled during fitting.

**PT uplift.** `c_pt(d) = c_car(d) * uplift`, where `uplift` is cited from
Huang & Levinson (2015). The value in the params CSV is currently an **UNVERIFIED
PLACEHOLDER** — it MUST be verified against the paper before the curve is used on
the pt axis in production. Do not treat the placeholder as a validated reference.

**Fit script and regenerate command.** `scripts/calibrate_detour_circuity.py`
reads the cached synpp working directory, extracts OD pairs (home→work via the
`synthesis.population.spatial.locations`/`activities`/`trips` join, car/walk
secondary legs by leg mode, education trips via `education_gravity`), builds
routing graphs from the **OSM PBF via pyrosm** — `car` = OSM driving network,
`walk` = OSM walking network, both **bbox-clipped to the ZGB home extent + margin**
(`scipy.sparse.csgraph.dijkstra` with a per-network distance `limit`; the MATSim
sim network is NOT used — it is too coarse and gave implausible circuity ~2.6x for
short trips). OD pairs are **clipped to the network coverage** (endpoints inside
the bbox, snap < 500 m); the cross-Germany commute/education tail is dropped and
logged. A **convergence-driven stratified-sampling loop** (minimum-samples floor
8000; stops when `c_inf`, `a`, `tau` are stable within tolerance for `patience`
rounds) fits each network. Zero new dependencies (scipy + pyrosm already present;
no networkx). NOTE: the OSM walk graph for ZGB is large (~1.9M nodes) and had
~31% route failures from disconnected pedestrian components in the 25% smoke — a
production activation of the curve should first restrict each graph to its largest
connected component.

```powershell
python scripts/calibrate_detour_circuity.py `
    --working-directory eqasim-data/cache_bs_25pct_allfeat `
    --osm-pbf eqasim-data/data/osm/cordon/germany-latest.zgb_ring.osm.pbf `
    --config config_server_braunschweig_25pct_allfeat_popsim.yml `
    --walk-route-limit-km 20 --car-route-limit-km 250 `
    --output-dir eqasim-data/data/braunschweig/calibration/detour
```

Outputs (the committed 25% measurement lives in `calibration/detour/`):
`detour_circuity_params.csv`, `circuity_convergence_<net>.csv/.png`,
`circuity_by_rs7.csv`, `band_shift_impact.csv` (commute EMD vs P13 and secondary
EMD vs W12 under constant 1.3 vs fitted curve — the materiality gate),
`circuity_fit_<net>.png`, `summary.md`, `PROVENANCE.md`.

**Per-RS7 diagnostic rule.** The script also reports a per-RS7 fitted curve
(`circuity_by_rs7.csv`). A per-RS7 curve is promoted only if the band-shift impact
diverges materially from the global curve (analogous to the education-style
shrinkage: sparse cells are shrinkage-regularised to the pooled curve of converged
cells; cells at the steep bound are kept as structural floors). Start with the
global curve; do not promote per-RS7 without evidence from `band_shift_impact.csv`.

**No behaviour change (default).** Because the curve is not pinned, the default
pipeline is byte-identical to the pre-Tier-3 constant `1.3`:
- `braunschweig/calibration/_legacy_education_slopes.py` (`--detour-factor` default
  `1.3`) — education slopes are calibrated on the constant; no re-pin was needed.
- `braunschweig/analysis/run_mid_validation.py` — education T43 targets use the
  constant `1.3` (`braunschweig.constants.ROUTED_DETOUR_FACTOR`).
- The fitted curve is reachable only via explicit `mode="curve"` (in
  `circuity_factor` / `apply_detour` / the education loaders) for future
  experimentation.

**Tier 3A — secondary scorer-weight calibration (built, NOT activated).**
`braunschweig/calibration/secondary.py` implements a pure coordinate-descent
optimiser (`coordinate_descent`) for per-purpose chainsolvers scorer weights
(`secondary_dist_dev_weight` / `secondary_scorer_pot_weight`). Infrastructure
only — pinning/activating the weights is gated on the deferred 25% ON validation
run actually showing a shop residual vs MiD W12. Until then the weights stay at
their current config values.

**Status of the once-deferred steps.**
1. DONE — `calibrate_detour_circuity.py` was run on the 25% cache; the measurement
   is committed under `calibration/detour/`.
2. DONE — `band_shift_impact.csv` shows the curve is NOT material -> education
   slopes were NOT re-pinned (they stay on the constant 1.3, unchanged).
3. OPEN (only if the curve is ever activated) — verify the pt uplift value against
   Huang & Levinson (2015) before using `mode="curve"` on the pt axis.
4. OPEN — Tier 3A (`secondary.py` scorer-weight descent) stays built-but-inactive;
   activation is still gated on a 25% ON validation run showing a shop residual.

Tests: `tests/test_circuity.py`, `tests/test_detour_fit.py`,
`tests/test_metrics_circuity.py`, additions to
`tests/test_mid_school_distance.py` / `tests/test_mikrozensus_school_distance.py`,
`tests/test_secondary_distance_dispatch.py`,
`tests/test_calibration_secondary_scorer.py`.
