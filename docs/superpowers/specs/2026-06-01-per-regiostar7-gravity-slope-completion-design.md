# Design: Complete the per-RegioStaR-7 gravity slope for Braunschweig

- Status: Draft (awaiting review)
- Date: 2026-06-01
- Branch: feature/education-gravity-bs
- Related: `scripts/calibrate_gravity_per_rs7.py`, `braunschweig/gravity/model.py`,
  `braunschweig/data/bbsr/regiostar.py`, `config_*braunschweig*.yml`

## 1. Goal

Give every ZGB-8 gravity origin a RegioStaR-7-type-specific distance-decay
slope. Today only RS7 codes **72** and **74** are calibrated, so **31 of 123**
origins (all Gemeinden with codes 73/75/76/77, plus one with no RS7 code) fall
back to the scalar `gravity_slope` (-0.065). The goal is full coverage
(123/123) using calibration anchors drawn from a data-driven distance ring
around ZGB, with the overall flow-weighted mean slope held invariant so the
regional commute total is unchanged.

This is a refinement of the existing per-RS7 mechanism (config comment in
`config_local_braunschweig.yml`, `_build_origin_slope_vector` in the gravity
model). It does **not** introduce a new modelling concept; it widens the
calibration anchor set and removes the silent scalar fallback.

## 2. Background / evidence

Established empirically on 2026-06-01:

- **Data is sufficient already.** The two BA Pendleratlas CSVs on disk
  (`statistik_pendler_2026042493412.csv` / `...430.csv`) contain a near-complete
  **all-Germany Kreis x Kreis matrix**: 400 origin Kreise, 48,340 OD pairs,
  every non-ZGB origin reaching a median 113 destinations. No new download is
  required to calibrate the missing RS7 codes.
- **Why only 72/74 today.** `scripts/calibrate_gravity_per_rs7.py` fits one
  Poisson-GLM slope per **ZGB-8 Kreis** (8 anchors) and aggregates by each
  Kreis's *dominant* RS7. The 3 Kreisfreie Stadte are dominant-72, the 5
  Landkreise dominant-74; codes 73/75/76/77 occur only at the Gemeinde level
  *within* those Kreise, so no ZGB Kreis is dominantly one of them. It is a
  resolution limitation of Kreis-level flow data plus dominant-RS7 aggregation,
  not an absence of those Raumtypen in the region.
- **ZGB RS7 composition (123 origins):** 72x3, 73x10, 74x89, 75x1, 76x3,
  77x16, plus 1 Gemeinde with no RS7 (Langelsheim, AGS 03153019, ~14.6k EW,
  LK Goslar; present in VG250, missing from the RegioStaR Gebietsstand-2020
  reference).
- **Scope matters (per-RS7 beta, rescaled to mean -0.065):**

  | RS7 | Germany | NDS | ring<=300km | ring<=150km |
  |----:|--------:|----:|------------:|------------:|
  | 71  | -0.062 (15) | - (0) | -0.072 (11) | -0.092 (1) |
  | 72  | -0.062 (55) | -0.054 (5) | -0.057 (41) | -0.058 (8) |
  | 73  | -0.067 (67) | -0.010 (2) | -0.067 (30) | -0.050 (3) |
  | 74  | -0.055 (41) | -0.078 (13) | -0.059 (27) | -0.061 (14) |
  | 75  | -0.059 (30) | -0.024 (2) | -0.058 (15) | -0.074 (2) |
  | 76  | -0.069 (25) | -0.068 (1) | -0.063 (17) | -0.048 (4) |
  | 77  | -0.069 (167) | -0.066 (22) | -0.068 (101) | -0.072 (28) |

  (n anchor Kreise in parentheses.) NDS-only is too sparse for 73/75/76
  (1-2 anchors -> implausible outliers). Germany is robust but flattens 74
  (-0.078 -> -0.055) and dilutes regional commute culture. A ~300 km ring is
  the sweet spot: enough anchors per code while staying regionally near.

- **Environment prerequisite (resolved).** The `eqasim` conda env's numpy was
  linked against the reference Netlib BLAS/LAPACK, which crashed natively on
  any LAPACK decomposition (`np.linalg.inv` on a 3x3 matrix already aborted;
  `GLM.fit` IRLS, `matrix_rank`, `svd`, `lstsq` all crashed). The synthesis
  pipeline was unaffected (it uses only BLAS matmul/`exp`). Fixed 2026-06-01
  by switching the backend to OpenBLAS
  (`conda install -n eqasim -c conda-forge libblas=*=*openblas
  liblapack=*=*openblas libcblas=*=*openblas`); `np.linalg` verified and the
  existing ZGB-8 calibration reproduces the committed 72/74 values exactly.

## 3. Requirements

- **REQ-1**: Calibrate a distance-decay slope for every RS7 code present among
  ZGB-8 origins (72,73,74,75,76,77). 71 does not occur in ZGB and is out of
  scope.
- **REQ-2**: Calibration anchors are selected by an **adaptive distance ring**
  around the ZGB centroid: grow the radius until every required RS7 code has at
  least `min_anchors_per_rs7` (default **5**) anchor Kreise. Emit the chosen
  radius and the per-code anchor counts.
- **REQ-3**: Preserve the existing rescale invariant: per-RS7 betas are scaled
  so their flow-weighted mean equals `gravity_slope` (-0.065). The regional
  mean commute distance must not change from this step.
- **REQ-4**: Every one of the 123 gravity origins receives a typed slope. A
  Gemeinde whose own RS7 is unknown is resolved by (a) a refreshed RegioStaR
  reference if it provides the code, else (b) geographic nearest-neighbour RS7.
  The scalar fallback must no longer be silently hit for ZGB origins.
- **REQ-5**: Reversibility. With the legacy 72/74-only dict, output is
  byte-identical to today (the existing `tests/test_gravity_slope_config.py`
  must stay green; `None` default behaviour preserved).
- **REQ-6**: Final slope values are **pinned by hand** into the configs from
  the calibration script's YAML output (project rule: no silently computed
  numbers; reproducible via the script).
- **REQ-7**: GLM fits that are under-identified (PerfectSeparation: n_obs not
  >> n_dest dummies) must be logged and either distance-band-trimmed or dropped
  from the anchor set, never silently kept.

## 4. Design

### 4.1 Adaptive-ring anchor selection (`calibrate_gravity_per_rs7.py`)

Generalise the calibration script:

- New CLI args: `--anchor-scope {zgb,nds,ring,germany}` (default `ring`),
  `--min-anchors-per-rs7` (default 5), `--max-radius-km` (safety cap, default
  e.g. 600).
- Compute each Kreis centroid's distance to the ZGB centroid (mean of the 8
  ZGB Kreis centroids).
- For `ring`: start from a small radius, expand stepwise; at each step compute
  the per-RS7 anchor count over Kreise within the radius; stop when every
  required RS7 code (those present in ZGB) reaches `min_anchors_per_rs7`, or
  the max radius is hit (then log which codes remain underfilled).
- Fit `fit_per_kreis_beta` for each anchor Kreis (unchanged method: Poisson-GLM
  with destination FE + distance), aggregate by dominant RS7 (existing
  `aggregate_by_rs7`), rescale to -0.065.
- Always emit a **sensitivity diagnostic** printing per-RS7 beta for zgb / nds /
  ring / germany side by side (the table in section 2), plus the chosen radius
  and anchor counts, so the pinned values are auditable.

### 4.2 GLM robustness (REQ-7)

Before fitting each Kreis, compare `n_obs` against the number of destination
dummies. If under-identified, first shrink the distance band for that Kreis;
if still under-identified, drop it from the anchors and log it. This removes
the PerfectSeparation noise that currently inflates the rescale factor (~6.3x)
without changing the estimator for well-identified Kreise.

### 4.3 Missing-RS7 resolution (REQ-4, two-tier)

In `braunschweig/data/bbsr/regiostar.py` (or a small helper consumed by it):

1. **Tier 1 - real value.** Refresh the RegioStaR reference to a Gebietsstand
   that includes Langelsheim (via the existing `scripts/download_regiostar.py`,
   newer sheet/file), or apply a DESTATIS AGS crosswalk, so 03153019 gets its
   official RS7. Pin the resulting reference like other inputs.
2. **Tier 2 - nearest-neighbour safety net.** For any Gemeinde still without an
   RS7 after tier 1, assign the RS7 of the geographically nearest Gemeinde that
   has one (centroid distance; RS7 is spatially autocorrelated). Log every such
   assignment.

This guarantees a typed slope for all 123 origins and generalises to any future
Gebietsstand gap. The coarse Kreis-dominant fallback is explicitly rejected.

### 4.4 Slope application (`braunschweig/gravity/model.py`)

- Extend the config dict to all six ZGB codes (72-77) with the pinned ring
  values.
- `_build_origin_slope_vector` continues to look up each origin's RS7 and apply
  the matching slope. Because the missing-RS7 resolution (4.3) now guarantees
  every Gemeinde has an RS7, and all six codes are in the dict, no ZGB origin
  hits the scalar fallback. The scalar default remains as the safety path for
  configs that omit the dict (preserving REQ-5 and the existing fix where the
  default is `None`).

### 4.5 Config + docs

- Add the pinned dict to `config_local_braunschweig.yml`, `_10pct`, `_25pct`,
  `_dryrun`, and `config_gravity_only_braunschweig.yml`.
- Document the adaptive-ring methodology and the missing-RS7 resolution in
  `CLAUDE.md` (gravity section) and note the refreshed RegioStaR vintage in
  `DOWNLOAD_CHECKLIST_BS.md` (B11).

## 5. Validation / acceptance

- **A-1**: `python -m scripts.calibrate_gravity_per_rs7 --anchor-scope ring`
  runs to completion, prints the sensitivity table, chosen radius, and a YAML
  dict covering 72-77.
- **A-2**: After pinning, a dry-run logs `per-RegioStaR slope active: 123/123
  origins overridden` (today: 92/123).
- **A-3**: All six slopes lie in a plausible band (about -0.04 .. -0.10) with a
  broadly monotone urban->rural gradient; deviations are explained in the
  calibration log.
- **A-4**: With the legacy 72/74 dict, `tests/test_gravity_slope_config.py`
  stays green and gravity output is unchanged (reversibility).
- **A-5**: Langelsheim (03153019) receives a typed slope via tier-1 or tier-2;
  the assignment is logged.

## 6. Out of scope

- Education gravity (`gravity_education_separate`) - separate plan.
- Per-Gemeinde (rather than per-RS7) slopes - rejected: Kreis-level flow data
  gives ~8 ZGB anchors and would overfit (RISK-004 in the education plan).
- Changing the Poisson-GLM estimator family or the destination-FE formulation
  beyond the under-identification guard in 4.2.
- Re-deriving RS7 classes from first principles.

## 7. Risks

- **R-1**: A wider ring imports non-local commute behaviour. Mitigated by the
  adaptive radius (smallest that satisfies the anchor threshold) and the
  rescale-to-mean invariant.
- **R-2**: Switching anchors changes the 74 slope materially (-0.078 -> ~-0.06).
  This is expected (the old value was NDS/ZGB-specific) but must be called out
  when the values are pinned, as it shifts the sub-Kreis distribution for the
  five Landkreise. Validate against MiD commute KPIs on a higher-sample run.
- **R-3**: A refreshed RegioStaR vintage could shift other Gemeinden's RS7
  codes. Mitigated by diffing the new reference against the pinned 2020 one and
  reviewing changes before adopting.
- **R-4**: Under-identified GLM fits. Addressed by 4.2.
