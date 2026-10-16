# Comparison tables of the parking cost zones v3 1 % arm set (issue #436, Part C)

Descriptive comparison of eight 1 % runs of the Braunschweig scenario. This is a comparison, never a validation: no table here
compares a model value with a validated reference, a run that converged (mode shares stabilised) is not a calibrated run, and
a 1 % run is not a validation run. The 25 % pair of the arm set is added to a sibling folder later.

## Inputs (read only)

Arm directories (downloaded comparison subsets of the felix runs, main checkout, `eqasim-data/output_bs_1pct_pv3_<arm>/`):

| arm | what differs from `v3` (resolved config of the arm) |
| --- | --- |
| `off` | `parking_zones_enabled: false`, `enable_urban_parking: false` (no parking cost at all) |
| `legacy` | `parking_zones_enabled: false`, `enable_urban_parking: true` (the old ring model, see caveats) |
| `v3` | the committed base: zones, garages, EV exemption, cost factor 1.0, free-share shift 0.0 |
| `v3_cost_0.5`, `v3_cost_2.0` | `parking_cost_utility_factor` 0.5 and 2.0 |
| `v3_free_shift_-0.1`, `v3_free_shift_-0.2` | `parking_workplace_free_share_shift` -0.1 and -0.2 |
| `v3_ev_off` | `parking_ev_exemption_enabled: false` |

Per arm: `matsim_output/modestats.csv`, `matsim_output/eqasim_trips.csv.gz` (final iteration 60 only),
`matsim_output/ITERS/it.51 .. it.60/<N>.parking_outcomes.csv` (zone arms, outcome CSV v4), the pipeline log
`parking_v3_<arm>.log` and the resolved config `parking_v3_<arm>_resolved.yml`. Pooled iterations: **51-60** (inclusive).
Committed inputs: the tariff table and the two SrV tables of `eqasim-data/data` of this worktree; the zone polygons of the
installed v3 release (SHA-256 prefix `7b200b87`, restricted file) from the main checkout. Every input path and SHA-256 is in
the two `*_provenance.json` files.

Code state: branch `feature/i436-v3-c-runs` at `0b5bea47` plus the helper `scripts/parking/summarize_arm_set.py` (this commit).
`configs/` is unchanged against the branch base `69fc8fc4` (review focus 5).

## Tables

| file | content |
| --- | --- |
| `parking_targets_comparison_1pct_pv3_2026-10-15.csv` (+ `_arm_deltas.csv`, `_provenance.json`) | output of `compare_parking_targets.py`, format unchanged: every metric with model value, SrV reference where one exists, universe and caveat; arm deltas against every earlier arm |
| `arm_set_summary_1pct_pv3_2026-10-15_modestats_pooled.csv` | per arm and mode: mean and sample SD (n - 1) of the MATSim `modestats` share over iterations 51-60, difference of the mean to `v3` (`diff_to_baseline_pp`) and to `off` (`diff_to_off_pp`), percentage points |
| `arm_set_summary_1pct_pv3_2026-10-15_zone_groups.csv` | per zone arm, zone group and purpose class: pricing calls, paid calls and share, expected garage share on paid and on free options, mean expected cost per call (unscaled euro cents), EV exempt, street infeasible, no feasible option and employer free counts |
| `arm_set_summary_1pct_pv3_2026-10-15_zone_group_deltas.csv` | each zone arm minus `v3` on the rows of the previous table (the table `v3_ev_off` is judged on) |
| `arm_set_summary_1pct_pv3_2026-10-15_free_share.csv` | per zone arm: configured shift, shift logged by the population stage, person-level free draw, pooled call-level EMPLOYER_FREE rate |
| `arm_set_summary_1pct_pv3_2026-10-15_provenance.json` | inputs with SHA-256, iterations |

Zone groups (`ZONE_GROUPS` in the helper, first match wins, the column `zone_ids` lists the zones of each group): `bs_zone_ia`,
`bs_zone_ib`, `bs_zone_ia_sued`, `bs_zone_ii`, `wob` (`wob_*`), `bh` (`bh_*`), `se_am_markt`, `other` (every other zone, e.g.
`pe_innenstadt`, `gs_*`, `sz_lebenstedt`, the `tu_*` campus zones). Purpose classes: `all` (home included), `work_education`,
`other_purposes` (not home, work, education).

## Commands (repository root of the worktree, Python of the `eqasim` environment, `PYTHONIOENCODING=utf-8`)

```
M=<main checkout>/eqasim-data ; A=$M/output_bs_1pct_pv3 ; O=docs/runs/artifacts/parking-v3-arms-2026-10-15

python scripts/parking/compare_parking_targets.py --data-path eqasim-data/data \
  --zones-geojson $M/data/braunschweig/parking/parking_zones_2026.geojson --iteration 51-60 \
  --arm-without-outcomes off=${A}_off/matsim_output --arm-without-outcomes legacy=${A}_legacy/matsim_output \
  --arm v3=${A}_v3/matsim_output --arm v3_cost_0.5=${A}_v3_cost_0.5/matsim_output \
  --arm v3_cost_2.0=${A}_v3_cost_2.0/matsim_output \
  --arm v3_free_shift_-0.1=${A}_v3_free_shift_-0.1/matsim_output \
  --arm v3_free_shift_-0.2=${A}_v3_free_shift_-0.2/matsim_output --arm v3_ev_off=${A}_v3_ev_off/matsim_output \
  --out $O/parking_targets_comparison_1pct_pv3_2026-10-15.csv

python scripts/parking/summarize_arm_set.py --data-path eqasim-data/data --iteration 51-60 \
  --arm-without-outcomes off=${A}_off --arm-without-outcomes legacy=${A}_legacy --arm v3=${A}_v3 \
  --arm v3_cost_0.5=${A}_v3_cost_0.5 --arm v3_cost_2.0=${A}_v3_cost_2.0 \
  --arm v3_free_shift_-0.1=${A}_v3_free_shift_-0.1 --arm v3_free_shift_-0.2=${A}_v3_free_shift_-0.2 \
  --arm v3_ev_off=${A}_v3_ev_off --out-dir $O --prefix arm_set_summary_1pct_pv3_2026-10-15
```

Tests of the helper: `python -m pytest tests/test_summarize_arm_set.py`.

## How to read the tables (caveats)

- **Sample size.** A 1 % run has few cars in the zones. Pooled over iterations 51-60 the zone `bs_zone_ia` has 228 pricing calls
  and `bs_zone_ib` 65 in arm `v3` (`zone_groups`, purpose class `all`), `bs_zone_ia_sued` and `bs_zone_ii` none; the
  work and education garage rows of the comparison table rest on tens of calls (`universe_size` of every row). Differences of
  one or two calls move a share by several percentage points. Treat every difference between two arms as one stochastic run each.
- **Counts are pricing calls, not trips or persons.** The outcome report counts car alternatives that mode choice evaluated,
  chosen or not, only for the agents that replan in an iteration; the calls of different iterations are not independent. The
  paid share is therefore an upper bound of a chosen-trip paid share (`NO_FEASIBLE_OPTION` and zero-cent `PAID_*` calls count as
  paid; the helper uses the `PAID_OUTCOMES` of the comparison script).
- **Mode shares.** `modestats` (MATSim's own mode statistics) and the `car_trip_share_*` metrics of the comparison table (the
  `eqasim_trips` table of the final iteration 60 only) are different universes and different iterations; they were not reconciled.
  The `car_trip_share_*` and `trips_*` rows use ONE iteration while the outcome rows pool iterations 51-60 (the script warns).
- **The SD is within-run.** `sd_share_within_run` is the spread of one run over its iterations 51-60 (serially correlated: the same
  agents keep their plans). It is NOT the noise between independent runs and not a standard error; no difference between two arms
  is tested. The arms share one seed and, for the cost arms, one cached population, so their differences are not independent draws.
- **Free share.** The person-level draw (`persons_drawn_free` of `persons_in_paid_zones`) is a share of persons in street and resident
  zones, taken from the pipeline log of the arm that built the population (the line appears once per population build). The
  call-level EMPLOYER_FREE rate is a share of pricing calls; the two are different measures and are shown next to each other,
  not as one number. `call_employer_free_share_draw_zones` uses the work and education calls in the zones of the free-draw zone types
  (street and resident), `..._all_zones` also the campus zones, whose free share has its own assumption and no shift.
- **`v3_ev_off` is compared on `zone_group_deltas`** (EV exempt counts and expected cost per zone group), not on mode shares
  (ruling R-C-3). The EV exemption acts only in the zones that carry an EV rule and only for the electric cars among the persons.
- **Legacy ring vs zones.** `legacy` prices parking with the old ring model (`enable_urban_parking`); `v3` prices by zone. They
  are different cost models with different universes, so only mode shares and trips are compared with them, never the
  pricing-call metrics (the table gives those rows empty with the reason).
- **No reference value is stated here.** The SrV references of the comparison table are read from the committed SrV tables and
  labelled there as comparison, calibration target or check; nothing in this folder claims that an arm matches observed behaviour.
