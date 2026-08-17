# Calibration corner + commute distance-distribution calibration


`braunschweig/calibration/` is the single home for the project's offline
calibration tooling. Runtime model components stay with the model (the
per-band friction builder lives in `braunschweig/gravity/friction.py`, the
secondary chainsolvers scorer in its own stage); the corner holds only the
shared metrics, MiD distribution targets, the per-model calibration loops,
their CLIs, and the reports. It consumes the runtime components and emits
pinned YAML; it is **never imported by the runtime pipeline**. The three
legacy calibrators were migrated in as `braunschweig/calibration/_legacy_*`
(gravity per-RS7 slope, gravity decay, education slopes) with thin
`scripts/calibrate_*.py` shims that preserve existing behaviour.

**Modules.**

- `metrics.py` — shared helpers: `band_shares`, `emd_on_bands`,
  `apply_detour` (`DETOUR_FACTOR = 1.3`, same ASSUMPTION and convention as
  T43; the model output is euclidean, the MiD target is routed, so model
  distances are scaled before comparison — the committed reference shares are
  never transformed).
- `targets.py` — MiD distribution-target loaders: `load_p13_band_shares`
  (per-Kreis commute bands from `mid2023_P13.csv`) and
  `load_p13_band_shares_by_rs7` (per-RS7 commute bands from
  `mid2023_P13_commute_distance_by_rs7.csv`).
- `commute.py` — Furness/Hyman multiplicative factor update (`furness_update`),
  sparse-cell shrinkage toward the pooled per-band factor
  (`shrink_sparse_factors`, rate logged — no silent fallback), and the
  end-of-calibration validation report (`build_validation_report`: per-Kreis
  distance EMD vs P13 target + per-Gemeinde attraction fill vs GENESIS SvB).

**The objective.** The gravity friction is calibrated so the realised
home -> work **straight-line distance distribution** matches MiD 2023 Tabelle
A P13 (EMD-minimised), not just the mean. There is no mode choice at this
stage (synthesis-realised, upstream of MATSim). The BA Pendleratlas Kreis-pair
calibration (`_calibrate` in `braunschweig.gravity.model`) is **unchanged and
always applied inside the loop** — it remains the authoritative inter-Kreis
control; the per-band friction factors only reshape the within-Kreis-pair
(including intra-Kreis) allocation.

**Per-band friction.** `braunschweig/gravity/friction.py` generalises the
scalar `exp(slope * d)` to per-band factors `f_b`, one per distance band,
wired into `braunschweig.gravity.model` behind config key
`gravity_friction_factors` (default `None` -> legacy exponential, OFF path
byte-identical). Global mode: `{band: f}`. Per-RS7 mode:
`{rs7: {band: f}}`, using the per-origin RS7 vector. Band edges (single
source of truth, aligned to MiD P13):
`BAND_EDGES_KM = (0, 5, 10, 20, 30, 50, 100, inf)` (7 bands).

**Reference data.** Two committed MiD CSVs under
`eqasim-data/data/braunschweig/mid/`:

| File | Source | Used by |
|---|---|---|
| `mid2023_P13.csv` | Tabelle A P13 per-Kreis + '03ZGB' aggregate | global calibration target |
| `mid2023_P13_commute_distance_by_rs7.csv` | Tabelle A P13 page 77, Raumtyp block, RS7 72–77 | `--per-rs7` calibration target |

The Raumtyp CSV is extracted by `scripts/extract_mid_p13_rs7.py` from the
local-only MiD PDF (page 77) via a PDF parser with an oracle assertion on all
6 rows (fail-fast on any PDF-extraction mismatch). RS7 code 71 (Metropole) is
absent from the ZGB sample.

**CLI — `scripts/calibrate_gravity_distribution.py`.** An in-process
Furness/Hyman loop on a cached working directory (no synpp re-run, no MATSim).
Per iteration: build friction matrix from current factors ->
`evaluate_gravity` -> `_calibrate` (BA pinned) -> row-normalise OD ->
sample work locations + measure realised straight-line distances ->
`band_shares(apply_detour(...))` -> EMD vs P13 -> `furness_update`. In
`--per-rs7` mode each RS7 (72–77) is updated independently toward its real
P13 Raumtyp target; an RS7 absent from the Raumtyp CSV falls back to the ZGB
aggregate with an explicit warning (CLAUDE.md no-silent-fallback). Sparse
`(RS7, band)` cells (count < `--min-count`, default 50) are shrinkage-blended
toward the pooled per-band factor; the shrinkage rate is always logged.
Acceptance criterion: commute EMD vs P13 <= `--emd-threshold` (default 0.08);
residual EMD from the BA inter-Kreis constraint is reported honestly. Outputs
under `--output-dir` (default
`eqasim-data/data/braunschweig/calibration/commute/`):
`gravity_calibration_results.csv` (per-Kreis band shares + EMD),
`gravity_calibration_results_per_rs7.csv` (per-RS7 mode only), and
`gravity_calibration_report.json`; the pinned YAML is printed to stdout.

**Workflow.** Develop and explore on `cache_bs_1pct_allfeat_full`; pin the
final `gravity_friction_factors` from `cache_bs_25pct_allfeat` (the 1 % cache
is too small for reliable per-Kreis x band cells). Run on the server where the
caches live:

```powershell
python scripts/calibrate_gravity_distribution.py `
    --working-directory eqasim-data/cache_bs_25pct_allfeat `
    --config eqasim-data/cache_bs_25pct_allfeat_popsim/.merged_config.yml `
    --per-rs7 `
    --output-dir eqasim-data/data/braunschweig/calibration/commute
```

(the composed run writes its exact resolved config to `.merged_config.yml`
inside its `working_directory`; see `configs/base_bs.yml`.)

If a calibration is warranted, paste the printed `gravity_friction_factors`
YAML block into the all-features run configs (do not hand-edit the factors —
re-run the script and paste its output).

**Finding (2026-06-25 run on `cache_bs_25pct_allfeat`): no commute friction
calibration is currently warranted.** Measured against MiD P13 (ZGB aggregate),
all inputs and the realised output already match: the per-person MiD work-leg
targets (the donor `commute_distance`) give EMD 0.0037, the gravity OD-flow
gives EMD 0.037, and the realised synthesis home->work straight-line
distribution gives EMD ~0.065 (below the 0.08 threshold). The historical
"EMD 0.47 FAIL" was a **stale** figure measured on MATSim-*routed* distances
from a run **before** the building-activity-potentials feature (which sources
work candidates from the gpkg buildings and reshaped the within-zone
placement). Because the distribution already matches, **no `gravity_friction_factors`
are pinned** — the per-band friction stays at its `None` default (byte-identical
to the legacy `exp(slope*d)` friction), and this module is provided as
calibration *infrastructure* (used if a future sampling rate, config, or the
education levels reveal a real distribution gap). A note on the discretization:
`synthesis.population.spatial.primary.locations.define_distance_ordering` is a
per-origin bijection between candidates and persons, so the greedy
target-matching is **aggregate-distribution-preserving** — the realised
trip-length histogram is governed by the OD-derived candidate pool (the
friction), not by the matching step.

Tests: `tests/test_gravity_friction.py`, `tests/test_calibration_metrics.py`,
`tests/test_calibration_targets.py`, `tests/test_calibration_commute.py`,
`tests/test_calibration_migration_shims.py`.
