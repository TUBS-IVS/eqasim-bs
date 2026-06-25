# CLAUDE.md

## Project context

This project uses MATSim and eqasim for scientific transport simulation research.

The codebase must be treated as research software. Correctness, reproducibility, traceability, clarity, and maintainability are more important than quick but fragile solutions.

The project should follow the style and structure of MATSim and eqasim as closely as possible. The implementation should be professional, modular, documented, and suitable for scientific use.

## Reference data: MiD 2023 constraint tables (read this!)

Numerical reference values from the MiD 2023 regional sample
are **not** stored as Python literals. They live as CSV files under
`eqasim-data/data/braunschweig/mid/` and are loaded by
`braunschweig.data.mid.reference_tables`:

| File | Source table | Consumed by |
|---|---|---|
| `mid2023_P19_car_constraints.csv` | A P19 'jederzeit' | `braunschweig.data.mid.data` |
| `mid2023_P22_bicycle_constraints.csv` | A P22 'ja' | `braunschweig.data.mid.data` |
| `mid2023_P24_1_pt_subscription_constraints.csv` | A P24.1 (sum of subscription columns) | `braunschweig.data.mid.data` |
| `mid2023_P24_1.csv` | A P24.1 raw 9-column ticket-type breakdown per Kreis | `braunschweig.synthesis.population.enriched` (categorical IPF) |
| `mid2023_P24_1_by_sex.csv` | A P24.1 sex margin (male/female × 9 ticket types) | same — IPF sex margin |
| `mid2023_P24_1_by_age.csv` | A P24.1 age margin (9 bands × 9 ticket types) | same — IPF age margin |
| `mid2023_H7_cars_by_kreis.csv` | H7 (Anzahl Autos im HH) | `braunschweig.synthesis.population.enriched` |
| `mid2023_H12_3_bikes_by_kreis.csv` | H12.3 (Fahrräder/Pedelecs im HH) | `braunschweig.synthesis.population.enriched` |
| `mid2023_H4_income_by_size.csv` | H4 (Ökonomischer Status × HH-Größe) | `braunschweig.data.census.household_income` |
| `mid2023_class_midpoint_eur.csv` | derived class-midpoint € lookup | `braunschweig.synthesis.population.enriched` |
| `mid2023_P17_1.csv` | A P17.1 raw 3-column licence breakdown per Kreis | `braunschweig.synthesis.population.enriched` (categorical IPF) |
| `mid2023_P17_1_by_sex.csv` | A P17.1 sex margin (male/female × {ja,nein,k.A.}) | same — IPF sex margin |
| `mid2023_P17_1_by_age.csv` | A P17.1 age margin (9 bands × {ja,nein,k.A.}) | same — IPF age margin |
| `mid2023_status_by_hhtype_bundesland.csv` | ökon. Status × Haushaltstyp × Bundesland | `braunschweig.data.mid.status_by_hhtype` (economic-status Bayes, NDS base) |
| `mid2023_status_by_hhtype_raumtyp.csv` | ökon. Status × Haushaltstyp × RegioStaR-7 Raumtyp | same — within-NDS raumtyp tilt |

The *additional* tables `mid2023_P9.csv`, `mid2023_P12_1.csv`, `mid2023_P13.csv`,
`mid2023_P17_1.csv`, `mid2023_P24_1.csv` are produced by
`scripts/extract_mid_tables.py` (PDF parser). The two
`mid2023_status_by_hhtype_*.csv` are produced by
`scripts/extract_mid_status_by_hhtype.py` from local-only raw xlsx exports.

### Economic status from MiD household-type × region (`status_from_hhtype`)

`economic_status` (5 BMDV classes very_low..very_high) is determined from the
much stronger **Haushaltstyp × Region** predictor via Bayes instead of being
mapped 1:1 from the income €-class. For each synthetic household:

- it is mapped to one of the 11 substantive MiD Haushaltstyp categories
  (`braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype`): 1-person
  by age band, 2-adult couple by youngest-adult age band, 3+ adults, child
  households bucketed by youngest-child age (<6 / <14 / <18), single parent
  (one adult + child, or upstream `hh_type=single_parent`);
- it is mapped to its home **RegioStaR-7** raumtyp (via `commune_id` → AGS-8 →
  `braunschweig.data.bbsr.regiostar`); Bundesland is always Niedersachsen;
- `P(status | hhtype, region) ∝ P(hhtype | status, region) · P(status | region)`
  with `P(hhtype|status,region)` = the column-% `share_pct` and `P(status|region)`
  from the per-(status,region) weighted bases. The **Bundesland (NDS) table is
  the base**; the raumtyp table is applied only as a **within-NDS tilt**
  (`P_raumtyp,region / P_raumtyp,national`) because the raumtyp table is national,
  not NDS-specific (`region_status_probabilities`).
- `economic_status` is sampled from this vector (seeded RNG, offset `+60413`),
  then `household_income` (€-class) is **re-derived from the sampled status**
  (`INCOME_CLASS_BY_ECONOMIC_STATUS`, the inverse of the H4 quintile map) so
  income and status agree; `household_income_eur` is then computed downstream by
  the existing INKAR class-midpoint scaling.

Flag-gated by `status_from_hhtype` (default **true**). OFF reproduces the exact
legacy path (commit c65399d): `economic_status` mapped 1:1 from the sampled
income €-class, income untouched → byte-identical. Households that cannot be
classified keep the legacy income-class status; the primary/fallback rate is
logged (CLAUDE.md no-silent-fallback). An **extension hook**
`bayes_status_given_hhtype_employment` is reserved for a future
`status × Erwerbstätigkeit` margin (multiplied in as a second Bayes factor).

Tests: `tests/test_status_from_hhtype.py`, additions in
`tests/test_economic_status.py`.

### PT ticket type (P24.1) — categorical & flatrate-derived `has_pt_subscription`

Each synthetic person receives a categorical attribute
`pt_subscription_type` ∈ `PT_TICKET_CATEGORIES` sampled from the per-Kreis
probability vector parsed from MiD 2023 Tabelle P24.1.
The boolean `has_pt_subscription` is then derived as

```
has_pt_subscription = pt_subscription_type ∈ PT_TICKET_FLATRATE
```

with `PT_TICKET_FLATRATE = {deutschlandticket, monat_abo_jahreskarte,
jobticket_semesterticket, wochen_monat_ohne_abo}` — i.e. all ticket types
that grant unlimited rides on local PT during their validity. The set is
defined in `braunschweig.data.mid.reference_tables` and re-used by the
MATSim person-attribute writer (`ptSubscriptionType` is written alongside
`hasPtSubscription`). The flatrate sum per Kreis matches the legacy
single-target seeding in `mid2023_P24_1_pt_subscription_constraints.csv`
within ±1 percentage point (covered by
`test_pt_flatrate_set_matches_legacy_kreis_share`).

The probability vector for each person is determined by a **three-margin
IPF (raking)** on the 4-way contingency table
`X[kreis, sex, age_bin, ticket_type]` with marginal targets from MiD P24.1:

- `mid2023_P24_1.csv`        — Kreis × ticket type
- `mid2023_P24_1_by_sex.csv` — Sex × ticket type (male/female)
- `mid2023_P24_1_by_age.csv` — Age × ticket type (9 bands: 14–17, 18–29,
  30–39, 40–49, 50–59, 60–64, 65–74, 75–79, 80+)

After convergence (200 iterations) every person in cell `(k, s, a)` is
assigned `P[k,s,a,:] = X[k,s,a,:] / Σ_c X[k,s,a,:]` and sampled
categorically.  Persons below `braunschweig.minimum_age.pt_subscription`
(default 0; effective floor is the MiD basis age 14) are deterministically
assigned `fahre_nie`.  Convergence diagnostics (max |Δ| per margin) are
printed by the `braunschweig.synthesis.population.enriched` stage — note
that MiD's three margins are independently rounded to integer percent and
therefore not internally consistent, so raking finds a least-squares
compromise within ~5 pp on the worst-case Kreis × ticket cell.

To **regenerate** the constraint CSVs from their pinned values run:

```powershell
python scripts/seed_mid_constraint_tables.py
```

This is the only supported way to update the values. Hard-coding new
percentages in Python modules is prohibited — add them to the seed
script (with a provenance comment) and re-run it instead.

Tests: `tests/test_mid_reference_tables.py` covers schema, loader
identity vs. legacy values, and seed-script idempotency.

### Driving licence (P17.1) — categorical & 3-margin IPF

`has_license` (renamed downstream to `has_driving_license`) is no longer
taken from KBA FE4.x data via the IPF model.  Instead each person above the
legal driving age (`LICENSE_MIN_AGE = 18`, regular Pkw-Führerschein Klasse
B; the BF17 / begleitetes Fahren option in Niedersachsen is intentionally
ignored) is assigned a `license_type`
∈ `LICENSE_CATEGORIES = ("ja","nein","keine_angabe")` sampled from a
**three-margin IPF (raking)** on the 4-way contingency table

```
Xl[kreis, sex, age_bin, license_category]
```

with marginal targets parsed from MiD 2023 Tabelle P17.1:

- `mid2023_P17_1.csv`        — Kreis × {ja,nein,k.A.}
- `mid2023_P17_1_by_sex.csv` — Sex × {ja,nein,k.A.}
- `mid2023_P17_1_by_age.csv` — Age × {ja,nein,k.A.} (9 MiD bands)

`has_license = (license_type == "ja")` (`keine_angabe` conservatively maps
to `False`, see `LICENSE_TRUE`).  Persons below 18 are forced to `"nein"`
deterministically.  The MiD margins are independently rounded to integer
percent and span 19 % … 94 %, so raking finds a least-squares compromise
within ~10 pp on the worst-case Kreis × age cell — diagnostics are printed
by the `braunschweig.synthesis.population.enriched` stage.

The legacy KBA-FE4-based `df["license"]` from
`braunschweig.ipf.attributed` is still produced (MiD overrides it inside
the enrichment stage), but is no longer the source of truth for
`has_license`.

Tests: `test_license_csv_has_all_kreise`,
`test_license_margin_csvs_exist_and_normalised`,
`test_license_margins_match_pdf_values`,
`test_license_ipf_three_margins_converges_on_synthetic_population`.

## IPF household synthesis: joint age x size margin (#3) + age-aware composition (#3b)

All household-synthesis features below are **flag-gated and default off**, so the
pipeline stays byte-identical to the legacy formation unless a config enables
them. They build on the per-commune household-size margin
(`braunschweig.ipf.use_household_size_margin`, Zensus 2022 1000A-2081).

### Joint age x household-size margin (#3)

`braunschweig.ipf.joint_age_size` adds the observed **age x household-size
correlation** to the IPF. The flat size margin balances size independently of
age, so the IPF would otherwise invent the joint (it would not know that large
households skew toward school-age children while 1-person households skew toward
the elderly). The joint is enforced at **Kreis** resolution over coarse age
groups, raked (2D IPF) to be consistent with BOTH the population age-group
marginal and the size marginal already in the IPF -- so adding it **cannot make
the IPF infeasible**. Source: Zensus 2022 **1000A-3082** (persons by
Gemeinde x age x sex x hh_size), loaded by
`braunschweig.data.census.households_size_age`. Enabled by
`braunschweig.ipf.use_joint_age_size_margin` (requires the size margin).

The coarse age groups are `DEFAULT_AGE_GROUP_BOUNDS = (15, 30, 40, 50, 60)` ->
`[0,15) [15,30) [30,40) [40,50) [50,60) [60,inf)`. **All edges are native
1000A-3082 ALTKL2 band edges** (0,5,10,15,20,25,30,40,50,60,75), so aggregating
the Zensus joint never splits a band (no assumption). The middle band is split at
**40 and 50** to give the joint a finer age resolution for family-size households
(real ZGB Zensus data show sizes 4/5/6+ concentrate in `[30,40)`/`[40,50)`, which
the old single `[30,60)` group could not pin). On its own this does **not** reduce
the parent-child age-gap tail -- that tail was dominated by a household-formation
routing bug (surplus children landing on elderly childless-shell adults), fixed
separately by the children-driven composition (see #3b below). Once that fix is in
place the finer bounds **do** reduce the residual tail: on the real ZGB IPF (25 %,
age-aware chunking) the parent-child gap>50 share falls 2.70 % -> 0.77 % and
gap>55 -> 0.03 % with the refined bounds vs the old `[30,60)` group. The bounds are
read from the config key `braunschweig.ipf.joint_age_group_bounds` (default =
`DEFAULT_AGE_GROUP_BOUNDS`), registered in both `braunschweig.ipf.prepare` and
`braunschweig.ipf.model` so a change correctly invalidates the synpp cache. A
**structural zero** (children below
`braunschweig.minimum_age.one_person_household`, default 16, in a 1-person
household) is held at exactly zero in the rake so it agrees with the IPF hard
zero (otherwise the full IPF diverges).

### Age-aware household composition (#3b)

`braunschweig.ipf.household_composition` + `form_households_age_aware`
(in `braunschweig.ipf.attributed`) replace the random within-bucket chunk + the
independent hh_type draw with one coupled, optimisation-based pass per
`(commune_id, hh_size)` bucket. Enabled by
`braunschweig.ipf.age_aware_chunking`. Adult/child composition per `hh_type` is a
HARD constraint; within it: couples are paired minimising the within-pair age gap
(jittered by `couple_age_std`, default 4.0, for a realistic spread), young
couples are routed to child-rearing households, and children are placed by a
**sorted rank match** (the 1-D optimum of the parent-child age-gap deviation
around a per-household target drawn `N(parent_child_gap_years, parent_child_gap_std)`,
defaults **31.8** = Destatis 2024 mean mother age at birth, **5.5**; clipped to
`parent_child_gap_max`, 50). The sorted match replaced a Hungarian
`linear_sum_assignment`: it is the same optimum but `O(n log n)` instead of
`O(n^3)`, which is essential because formation runs on the **full** population
(the attributed stage is upstream of sampling, so even a 25 % output forms
households on all ~1.13 M persons; the dense LAP was a hard wall on large urban
buckets). hh_type counts per bucket are allocated by the largest-remainder method,
but **children drive the composition**: `_ensure_child_capacity` grows the
child-bearing capacity until it covers every child in the bucket (the IPF places
more children in a cell than the Zensus single_parent share provides shells for),
so no surplus child spills onto the oldest childless-shell adults. Without this,
~23 % of placed children had a youngest household adult 55+ years older (mean 84 --
implausible "single parents"); with it the gap>55 tail drops to ~0.3 % (~0.03 %
with the refined bounds) and the mean gap from 39 to 26 years.

The children-driven fix gives child households the youngest adults, which pulls
the realised mean gap *below* the target (26 vs 31.8: 18-25-year-olds become
parents of newborns). `child_parent_age_target_weight` corrects this -- child
households claim a contiguous window of the age-sorted adults centred on
(median child age + gap) rather than the absolute youngest, leaving the very
youngest adults for childless young couples/singles. The weight blends from 0
(youngest, mean 26) to 1 (fully targeted, mean 33); the default **0.85** is
calibrated on the real ZGB IPF to a realised mean of **31.8** (= Destatis
`parent_child_gap_years`), with the gap>55 tail unchanged at ~0.04 %.

No person is ever dropped; **all-children households are hard-blocked**
(in-bucket merge + a global
cross-bucket same-commune merge). Config keys live under `braunschweig.chunking.*`.

**Sex-aware couple pairing.** With `braunschweig.chunking.sex_aware_couples` on,
couples are paired **opposite-sex by default** with a small calibrated same-sex
share `braunschweig.chunking.same_sex_couple_share`
(`DEFAULT_SAME_SEX_COUPLE_SHARE = 0.011`). Provenance: Statistisches Bundesamt,
**Mikrozensus 2025**, Tabelle "Gleichgeschlechtliche Lebensgemeinschaften" --
204 000 same-sex couples (102k male / 102k female, ~50/50) against ~18.9 M
couples => ~1.1 %. Pairing is `pair_adults_sex_aware`, an **opposite-first**
allocation: the number of same-sex couples in a block is
`max(intended, forced)`, where `intended ~ Binomial(k, share)` is the genuine
share and `forced = |#males - #females| / 2` is the minimum imposed by the
block's sex imbalance (so nobody is dropped). Within each group, partners are
paired adjacently in jittered-age order (small within-couple gaps, opposite pairs
rank-aligned). The 50/50 male/female split emerges from the balanced pool.
Default off (`is_female=None`) -> the legacy sex-blind age-adjacent pairing,
byte-identical.

The realised share **converges toward 1.1 % as the sampling rate rises** because
the per-(commune, hh_size) bucket imbalance floor shrinks: on the cached ZGB
population it is ~4.8 % at 5 %, **~2.9 % at 25 %**, and approaches the ~1.1 %
target at 100 % (the residual is the genuine local sex imbalance in small
Gemeinden). For contrast, the sex-blind pairing yields **~48 %** same-sex couples
(every age-adjacent pair is sex-random) -- the reason the feature exists.

Tests: `tests/test_joint_age_size.py`, `tests/test_household_composition.py`,
`tests/test_run_household_composition.py`.

## Gravity model: per-RegioStaR-7 distance slope

`braunschweig.gravity.model` distributes work/education trips with a
distance-decay friction `exp(slope * d_ij)`. The `slope` is differentiated by
the **RegioStaR-7** class (BMV/BBSR urban-rural typology, codes 71-77) of the
origin Gemeinde, so urban origins (flatter slope, longer commutes) and rural
origins (steeper slope, shorter commutes) decay at their own rate. The
flow-weighted mean of the per-class slopes is held equal to `gravity_slope`
(-0.065), so the regional mean commute distance is unchanged; only the
sub-Kreis distribution is differentiated (the commute-distance KPI itself is
MiD-P13-overridden, see `commute_distance.py`).

Calibration (`scripts/calibrate_gravity_per_rs7.py --anchor-scope ring`) fits a
single **identified full-panel Poisson GLM** on the BA Pendleratlas Kreis-pair
flows:

```
log E[flow_ij] = origin_FE_i + dest_FE_j + sum_c delta_c * d_ij * 1[RS7(i)=c]
```

A per-origin fit with destination fixed effects is rank deficient on this data
(one flow row per origin-destination pair makes distance collinear with the
per-destination dummies), so the full panel is used: each `delta_c` is
identified from within-origin distance variation pooled across the many origins
of class `c`. The anchor Kreise are chosen by an adaptive ring that grows around
ZGB until every RS7 code present in ZGB has at least 5 anchors (225 km / 141
Kreise at present). Pinned values live in `config_*braunschweig*.yml` under
`gravity_slope_by_regiostar7` (do not hand-edit; re-run the script and paste its
YAML). `braunschweig.data.bbsr.regiostar` assigns every in-scope Gemeinde an RS7
code, filling Gemeinden absent from the RegioStaR-2020 reference (e.g.
Langelsheim, 03153019) by geographic nearest neighbour, so all 123 gravity
origins receive a typed slope.

Tests: `tests/test_gravity_ring_calibration.py` (ring selection + panel
recovery), `tests/test_regiostar_fill.py` (nearest-neighbour fill),
`tests/test_gravity_slope_config.py` (the `None` default / flatten contract).

## Calibration corner + commute distance-distribution calibration

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
    --config config_server_braunschweig_25pct_allfeat_popsim.yml `
    --per-rs7 `
    --output-dir eqasim-data/data/braunschweig/calibration/commute
```

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

## Distance-dependent detour/circuity factor (Tier 3)

**Premise.** Every calibrator in the corner converts model output (straight-line
euclidean km) to the routed axis of MiD band edges by multiplying by a detour
factor. The legacy constant `1.3` is a broad average; empirical network studies
show circuity decays with distance (Ballou et al. 2002; Giacomin & Levinson 2015,
*Road network circuity in metropolitan areas*) — short trips are more tortuous than
long ones. Replacing `1.3` with an empirically fitted curve improves the axis
alignment of **every** distribution comparison and is therefore a methodological
correctness measure, not a fix for a failing KPI (shop EMD 0.053, commute EMD ~0.065
— both already pass the 0.08 threshold). The fit script's `band_shift_impact.csv`
quantifies the materiality of the curve vs the constant before any slopes are
re-pinned (measure-first gate).

**Curve form.** `c(d_km) = c_inf + a * exp(-d_km / tau)` (per network). Both
directions are exposed:
- `euclidean_to_routed(d)` = `d * c(d)` — converts model output to the routed axis.
- `routed_to_euclidean(r)` — unique inverse via `scipy.optimize.brentq`
  (converts MiD routed targets to straight-line for slope calibration).

**Module.** `braunschweig/calibration/circuity.py` — contains
`circuity_factor`, `euclidean_to_routed`, `routed_to_euclidean`, and
`load_circuity_params`. `braunschweig/calibration/metrics.py` (`apply_detour`)
delegates to this module.

`mode="curve"` (NEW DEFAULT) uses the fitted curve; `mode="constant"` reproduces
the legacy `* 1.3` exactly for reproducibility / regression.

**Single source of truth for params:**
`eqasim-data/data/braunschweig/calibration/detour_circuity_params.csv` (local-only,
not committed). Car and walk rows carry `c_inf`, `a`, `tau_km`; the pt row carries
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

**PT uplift.** `c_pt(d) = c_car(d) * uplift`, where `uplift` is cited from
Huang & Levinson (2015). The value in the params CSV is currently an **UNVERIFIED
PLACEHOLDER** — it MUST be verified against the paper before the curve is used on
the pt axis in production. Do not treat the placeholder as a validated reference.

**Fit script and regenerate command.** `scripts/calibrate_detour_circuity.py`
reads the cached synpp working directory, extracts OD pairs (home→work, car/walk
secondary legs, education trips), builds routing graphs — car from the MATSim
`supply.processed` network (`read_matsim_network` in `detour_fit.py`, xml.etree
iterparse + `scipy.sparse.csgraph.dijkstra`; the repo-local `matsim` package is
deliberately NOT imported), walk from the OSM PBF via pyrosm — and runs a
**convergence-driven stratified-sampling loop** (minimum-samples floor 8000; stops
when `c_inf`, `a`, `tau` are all stable within tolerance for `patience` rounds).
Zero new dependencies beyond what scipy already provides.

```powershell
python scripts/calibrate_detour_circuity.py `
    --working-directory eqasim-data/cache_bs_25pct_allfeat `
    --osm-pbf eqasim-data/osm/niedersachsen.osm.pbf `
    --config config_server_braunschweig_25pct_allfeat_popsim.yml `
    --output-dir eqasim-data/data/braunschweig/calibration/detour_circuity
```

Outputs: `detour_circuity_params.csv` (committed path updated in-place),
`circuity_convergence_<net>.csv/.png`, `circuity_by_rs7.csv`,
`band_shift_impact.csv` (commute EMD vs P13 and secondary EMD vs W12 under
constant 1.3 vs fitted curve), `circuity_fit_<net>.png`, `summary.md`.

**Per-RS7 diagnostic rule.** The script also reports a per-RS7 fitted curve
(`circuity_by_rs7.csv`). A per-RS7 curve is promoted only if the band-shift impact
diverges materially from the global curve (analogous to the education-style
shrinkage: sparse cells are shrinkage-regularised to the pooled curve of converged
cells; cells at the steep bound are kept as structural floors). Start with the
global curve; do not promote per-RS7 without evidence from `band_shift_impact.csv`.

**Behaviour change.** Two existing tools switch from the constant to the curve by
default:
- `braunschweig/calibration/_legacy_education_slopes.py` (`--detour-factor`,
  default now `None` = curve; pass `--detour-factor 1.3` to reproduce the old fit).
- `braunschweig/analysis/run_mid_validation.py` (`detour_factor=None` => curve for
  education T43 targets, labelled "Tier 3C" in the source).
- The constant remains reachable via `mode="constant"` in `circuity_factor` /
  `apply_detour`, or via `braunschweig.constants.ROUTED_DETOUR_FACTOR` (value 1.3).

**Tier 3A — secondary scorer-weight calibration (built, NOT activated).**
`braunschweig/calibration/secondary.py` implements a pure coordinate-descent
optimiser (`coordinate_descent`) for per-purpose chainsolvers scorer weights
(`secondary_dist_dev_weight` / `secondary_scorer_pot_weight`). Infrastructure
only — pinning/activating the weights is gated on the deferred 25% ON validation
run actually showing a shop residual vs MiD W12. Until then the weights stay at
their current config values.

**SERVER-DEFERRED steps (require the 25% allfeat cache and OSM PBF).**
1. Run `calibrate_detour_circuity.py` and commit the updated params CSV.
2. Check `band_shift_impact.csv` — if the shift is material, re-run
   `calibrate_education_slopes.py` (without `--detour-factor 1.3`) and re-pin
   the `education_gravity_slope_by_level_rs7` YAML in the run configs.
3. Verify the pt uplift value against Huang & Levinson (2015) and update the pt
   row in the params CSV.
4. Re-run `validate_secondary_distances.py` under the curve default and assess
   whether Tier 3A activation is warranted.

Tests: `tests/test_circuity.py`, `tests/test_detour_fit.py`,
`tests/test_metrics_circuity.py`, additions to
`tests/test_mid_school_distance.py` / `tests/test_mikrozensus_school_distance.py`,
`tests/test_secondary_distance_dispatch.py`,
`tests/test_calibration_secondary_scorer.py`.

## Education gravity model (NDS school data)

All education levels are assigned by real-data distance-decay gravity models,
replacing the generic OSM hard-radius sampler: school-age pupils (6-19) to **real
Niedersachsen schools**, kindergarten children (0-5) to **real Kita facilities**
(LSN Plaetze), and university students (20+) to **real Hochschulen** (LSN
enrollment). The feature is flag-gated; with `education_gravity_enabled=false`
(default) the pipeline is byte-identical to the legacy OSM education assignment.

**Data.** The facilities table
`eqasim-data/data/braunschweig/schools/nds_schools_zgb.csv` (kept **local only**
for data-protection -- not committed; the `eqasim-data` tree is gitignored) is
built by `scripts/extract_nds_schools.py`
from the LSN directories `Schulverzeichnis_ABS_2025.xlsx` (allgemeinbildende
Schulen) + `Verzeichnis_der_BBS_2024.xlsx` (berufsbildende Schulen). One row per
**(school, level)**: a school offering several levels (e.g. a KGS) appears once
per level with that level's real pupil count as `capacity`. The script geocodes
addresses via OSM Nominatim (1 req/s, cached) and validates each point offline
against the local OSM education POIs (`osm_pois.parquet`, distance to the nearest
education feature; `validated = dist < 750 m`). Full provenance + the regenerate
command live in `eqasim-data/data/braunschweig/schools/README.md` and the
end-to-end trace in `.../schools/DATA_FLOW.md`. Hard-coding coordinates or
capacities in Python is prohibited - change the xlsx source or
`braunschweig/data/schools/typing.py` and re-run the script.

**Age -> level + capacity.** `braunschweig.data.schools.typing` maps each LSN
Schulgliederung (SGL) code to one of FOUR school levels and sums the matching
pupil counts: Primarbereich (SGL 00,01,03,04) -> `grundschule` (6-9);
Haupt/Real/Gym-SekI/IGS/KGS (11-19) plus the Oberschule/Foerderschule block
(40-69) -> `sekundar_1` (10-15); Gym/IGS/KGS Sek II (23,24,28,29) -> `oberstufe`
(academic upper secondary); all BBS pupils -> `bbs` (vocational). Adult forms
(Abendgymnasium 30, Kolleg 31) are excluded. Age 16-19 pupils are split per
person between `oberstufe` and `bbs` by `education_bbs_share` (default 0.681 =
NDS enrollment BBS 29336 / (BBS 29336 + Oberstufe 13745)). The split matters
because the two have very different trip lengths: BBS are sparse with a regional
catchment (long trips), the gymnasiale Oberstufe is local. The
Gymnasium/Realschule/Hauptschule mix within a level emerges automatically from
the real per-level capacity shares (no school-track choice is modelled). Note the
gravity age bands (0-5 / 6-9 / 10-15 / 16-19 / 20+) reclassify the boundary ages
relative to the legacy OSM sampler's 0-6 / 7-17 / 18+ split: with the flag ON,
age 6 moves from kindergarten to `grundschule` and ages 18-19 from university to
oberstufe/bbs. This only affects the ON path; the OFF path keeps the legacy
bands. LSN internal codes drop the Land prefix: official AGS-8 = `"03" + AGS6`,
Kreis-5 = `"03" + Kreis3`; the table is filtered to the ZGB-8 Kreise.

**The model (capacity-constrained distance decay).** Per level, the assignment is
a **rectangular doubly-constrained Furness balancing**
(`braunschweig.synthesis.locations.education_gravity_model.balance_doubly_constrained`,
the rectangular generalisation of `braunschweig.gravity.model.evaluate_gravity`):
pupils are rows (production target 1 each -> everyone is placed), schools are
columns (attraction target = real `capacity` **scaled to the pupil count** ->
schools fill in proportion to real Schuelerplaetze), friction
`f = exp(slope_level * d_km)`. Each pupil then draws a school proportional to the
**balanced flow row** - so distance decay shapes the assignment while the
double-constraint prevents a tiny nearby school from swallowing pupils that belong
in a larger one ("no 2-vs-10000"). A per-level max radius bounds the candidate set
(nearest-school fallback when a pupil has none in range). All randomness uses the
single `random_seed`. Kindergarten (0-5) uses the SAME doubly-constrained capacity
gravity on the Kita facilities (see below); university (20+) uses a singly-
constrained decay (see below). The per-person stage
`braunschweig.synthesis.locations.education_gravity` produces the legacy output
schema `[person_id, commune_id, location_id, geometry]` and is swapped in by the
flag-gated wrapper
`braunschweig.locations.synthesis.replacement_education_gravity` (aliased to
`synthesis.population.spatial.primary.locations`).

Config keys (defaults in the stage's `configure`):
`education_gravity_enabled` (false), `education_gravity_slope_by_level`
(`{grundschule, sekundar_1, oberstufe, bbs}`),
`education_gravity_max_radius_km_by_level` (includes `kindergarten`),
`education_gravity_max_iterations` (50), `education_gravity_tolerance` (1e-3),
`nds_schools_path`, plus the kindergarten + university keys below.

**Kindergarten (Kita) children (age 0-5).** Routed through the SAME
doubly-constrained capacity gravity as the schools, on real Kita facilities
(`braunschweig.data.schools.kita_facilities`). Capacity = the LSN
Kindertageseinrichtungen **Plaetze** per Einheits-/Samtgemeinde (local-only
`eqasim-data/data/braunschweig/schools/nds_kitas_zgb.csv` from LSN table K2300112,
extracted by `scripts/extract_nds_kitas.py`; ZGB-8 = 832 facilities / 56084
Plaetze). The Samtgemeinde Plaetze are distributed across the unit's OSM
kindergarten POIs by area: each POI's LSN unit code is derived from its 12-digit
ARS commune_id as `ARS[2:5] + ARS[6:9]` (Kreis + Verband), with a 3-digit Kreis
fallback for the kreisfreie Staedte (BS/SZ/WOB, which LSN lists at Kreis level) --
this needs no separate Samtgemeinde membership table. The per-RS7 slope is
calibrated against the MiD 2023 Tabelle 43 **0-6** column (~1.5-2.3 km
straight-line; RS7 72 floors at ~1.6 km = the nearest urban Kita). The LSN table
K2300223 (children in Kita by age group + Besuchsquote) is a local validation
reference, not a model input. Config: `nds_kitas_path`, the `kindergarten` entries
in `education_gravity_slope_by_level_rs7` / `education_gravity_max_radius_km_by_level`
(8 km).

**University (Hochschule) students (age 20+).** University students are routed
through a dedicated
**singly-constrained** distance-decay model (`assign_by_decay`): each student
draws an institution `~ enrollment_j * exp(slope * d_ij)` within
`education_university_max_radius_km` (150 km), with a nearest-campus fallback.
Singly-constrained (NOT the doubly-constrained school model) is the key choice:
far universities (Goettingen, Hannover) have huge enrollment that is mostly
non-resident, so only the distance decay -- not a hard capacity target -- should
govern how far the local commute tail reaches. Destinations come from
`braunschweig.data.schools.university_facilities`: real LSN SS2025 enrollment per
institution (local-only `eqasim-data/data/braunschweig/schools/nds_hochschulen.csv`,
seeded by `scripts/seed_nds_hochschulen.py`) -- inside ZGB the per-commune
enrollment is spread across the commune's OSM university buildings by area (TU BS +
HBK pooled in 03101; Ostfalia split across 03158/03102/03103; TU Clausthal 03153);
each of the 12 surrounding institutions (Hannover cluster, Goettingen, Hildesheim,
HAWK, plus the cross-border Magdeburg OVGU / HS Magdeburg-Stendal and Hochschule
Harz, Leuphana Lueneburg) is a single curated campus point. The single national
`education_university_slope` (-0.1415) is calibrated so the mean ZGB student commute
matches the **Destatis MZ 2024 Hochschule** mean (~15.2 km straight-line); the
result is ~91 % local (TU BS / Ostfalia / Clausthal / HBK) and ~9 % commuting to
Hildesheim / Hannover / Harz / Magdeburg / Goettingen. Config:
`education_university_slope`, `education_university_max_radius_km`,
`nds_hochschulen_path`.

**Enrollment report (debug / calibrate).**
`python -m braunschweig.analysis.run_education_validation --working-directory
<cache> --sampling-rate <r> --output-dir <out>` writes
`school_enrollment_vs_capacity.csv` (per school: capacity vs assigned pupils
scaled to 100 %, fill_ratio) and `level_summary.csv` (per level: pupil count,
mean/median straight-line school-commute km), so over-/under-filled schools and
the slope calibration are immediately visible.

**Per-(RegioStaR-7, level) slope calibration (MiD Tabelle 43).** The decay slope
is differentiated by the pupil's **home RegioStaR-7** class so urban pupils (short
trips) and rural pupils (long trips) decay at their own rate. Each pupil's home
RS7 comes from a spatial join of the home point to `data.spatial.municipalities`
(the 12-digit ARS is converted to the 8-digit AGS via
`braunschweig.data.bbsr.regiostar.ars_to_ags8` before the RS7 merge -- without
this every pupil silently falls back to the scalar slope). The per-RS7 slopes
live in `education_gravity_slope_by_level_rs7` (nested `{level: {rs7: slope}}`;
default `None` -> scalar `education_gravity_slope_by_level`, like
`gravity_slope_by_regiostar7`). They are calibrated against **MiD 2023 Tabelle 43**
("Kita- und Schulweglaengen nach Raumtyp und Altersgruppe", reference CSV
`eqasim-data/data/braunschweig/mid/mid2023_T43_school_distance_by_rs7.csv` seeded
by `scripts/seed_mid_t43_school_distance.py`, loaded by
`braunschweig.data.mid.school_distance`). The MiD age groups map 0-6 ->
kindergarten, 7-10 -> grundschule, 11-13 -> sekundar_1, 14-17 -> `oberstufe`; MiD
routed lengths are divided by a detour factor (1.3) to a straight-line target. The
vocational `bbs`
level has no per-RS7 MiD target -- BBS distance is benchmarked against the
**Destatis Mikrozensus 2024** national school-trip distribution by school type
(`braunschweig.data.mikrozensus.school_distance`, CSV seeded by
`scripts/seed_mikrozensus_school_distance.py`): the banded BBS distribution gives
a national straight-line mean of ~15.8 km, applied as the same target to every RS7.

`scripts/calibrate_education_slopes.py` runs the calibration on the 25 % synthesis
(`cache_bs_25pct`): the WHOLE level is assigned each round (per-pupil slope vector
by home RS7) and each RS7's mean trip distance is moved toward its target by a
per-RS7 **bisection** (`calibrate_level_per_rs7`; bisection is stable on the noisy
means of small rural cells). Calibrating cells in isolation is wrong -- the
capacity constraint, scaled to a pupil subset, forces filling out-of-catchment
schools. Tiny/sparse cells off by > 1.5 km whose slope is NOT at the steep bound
are **shrinkage-regularised** to the pupil-weighted mean slope of the converged
cells of the same level; cells AT the steep bound (slope ~ -3.0) are kept -- there
the target is simply below the nearest-school distance (rural BBS / rural
Oberstufe), a legitimate structural floor, not noise. The committed evaluation
(`--output-dir eqasim-data/data/braunschweig/mid/education_calibration/`:
`calibration_results.csv`, two figures, `calibration_summary.md`) shows
grundschule, sekundar_1 and bbs hit their targets across RS7 (bbs RS7 77 floors at
~20 km -- the nearest rural BBS is already that far); oberstufe converges for the
larger cells, while the tiny rural cells (RS7 75/76/77, ~40-50 pupils at 25 %) are
regularised and would sharpen at a higher sampling rate. Re-run the script
(`--bbs-share` controls the upper-secondary split) and paste its YAML to update the
slopes; do not hand-tune.

Tests: `tests/test_school_typing.py`, `tests/test_school_readers.py`,
`tests/test_school_facilities.py`, `tests/test_education_gravity_model.py`,
`tests/test_education_gravity_stage.py`, `tests/test_education_validation.py`,
`tests/test_mid_school_distance.py`, `tests/test_mikrozensus_school_distance.py`,
`tests/test_university_facilities.py`, `tests/test_extract_nds_kitas.py`,
`tests/test_kita_facilities.py`, `tests/test_calibrate_education_slopes.py`,
`tests/test_regiostar_fill.py` (the `ars_to_ags8` helper).

## Building-level activity potentials

Building-level activity potentials redistribute synthetic activity locations
(work, secondary, education) from the commune/zone level down to individual
OSM/ALKIS buildings. Without this feature every person's activity is placed at
a zone centroid or a uniform random building; with it, buildings are weighted
by their floor-area-based activity potential so that large offices, shopping
centres, and schools attract proportionally more trips.

**Data source.** The potentials are derived from OSM footprints and ALKIS
building attributes by the **TUBS-IVS
Activities-and-Potentials-Calculation-Pipeline** (separate repository). The
output is a local-only parquet file:

```
eqasim-data/data/braunschweig/buildings/building_activity_potentials.parquet
```

This file is **not committed** (large, derived, local-only). The pipeline that
generates it is the canonical source of truth; hard-coding building coordinates
or capacity values in Python is prohibited. Regenerate with the Activities-and-
Potentials-Calculation-Pipeline and copy the output to the path above.

**Stage.** `braunschweig.data.building_potentials` validates the parquet on load
by calling `validate()`, which **raises** if the file is absent or malformed
(fail-fast, no silent fallback). The stage is consumed by the three downstream
feature stages that redistribute locations.

**Feature flags (all default true in code; OFF in non-real-data configs).**

| Config key | Stage / effect |
|---|---|
| `work_building_potentials` | `braunschweig.synthesis.locations.work` — weighted building draw for work locations |
| `secondary_building_potentials` | `braunschweig.synthesis.locations.secondary` — weighted building draw for secondary locations |
| `secondary_scorer_mode` | `"combined"` uses both potential and distance deviation; `"distance"` reverts to distance-only |
| `secondary_scorer_pot_weight` | Weight on the potential term in the combined scorer (default `1.0`) |
| `secondary_scorer_dist_dev_weight` | Weight on the distance-deviation term in the combined scorer (default `1.0`) |
| `education_building_distribution` | `braunschweig.synthesis.locations.education_gravity` — weighted building draw within the assigned school/facility |

**Run config split.** The five committed real-data run configs
(`config_local_braunschweig.yml`, `config_server_braunschweig_100pct.yml`,
`config_server_braunschweig_1pct_allfeat_popsim.yml`,
`config_server_braunschweig_25pct_allfeat_popsim.yml`,
`config_freight_validate.yml`) set all four flags to `true` and include
`building_potentials_path`. The local-only (gitignored)
`config_local_braunschweig_1pct_allfeat_full.yml` also enables the feature
but is not committed. All other configs set the three boolean flags to `false`
and omit the path, so the feature is off and no local-only parquet is required.

**Aggregate controls are unaffected.** Work-zone totals (GENESIS SvB), OD
gravity flows, and NDS school enrollment totals remain the authoritative
controls. The building potentials only govern the *within-zone / within-school*
spatial distribution of already-placed activities.

**OFF path** (`work_building_potentials: false`, `secondary_building_potentials:
false`, `education_building_distribution: false`) is byte-identical to the
pre-feature pipeline: activity locations are placed by the existing zone-level
or uniform-random-building logic, no building parquet is loaded, and the
`braunschweig.data.building_potentials` stage is never requested.

Tests: `tests/test_building_activity_potentials_import.py`,
`tests/test_building_activity_potentials_stage.py`,
`tests/test_building_potential_attach.py`,
`tests/test_work_building_potentials.py`,
`tests/test_secondary_building_potentials.py`,
`tests/test_education_building_distribution.py`.

## Purpose-resolved secondary activity distances (Tier 1 + Tier 2)

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

## Long-haul freight injection (german-wide-freight v3)

Long-haul road freight (heavy goods vehicles) is injected into the MATSim
scenario from the VSP **german-wide-freight v3** model (Lu, Martins-Turner,
Nagel 2022, *A simple, calibrated, agent-based, German-wide freight transport
model*, doi:10.1016/j.procs.2022.03.080). The freight plans and the
Germany-wide road network are **local-only** (large, not committed; the
`eqasim-data` tree is gitignored) and are fetched by
`scripts/download_german_wide_freight.py`, which prints the exact download
command when an input is missing. All freight geometry is processed in
**EPSG:25832** (the project-wide metric CRS).

Relative to the ZGB study area each freight trip is one of four categories:
**INTERNAL** (origin and destination both inside ZGB), **INCOMING**
(destination inside, origin outside), **OUTGOING** (origin inside,
destination outside) and **TRANSIT** (through-traffic: both endpoints outside
ZGB but the routed path crosses the study area). Determining the TRANSIT share
correctly requires **routing each freight trip on the German-wide road
network** and testing the route against the study-area polygon -- a
straight-line OD test would miss exactly the through-traffic that uses the ZGB
motorways (A2, A7, A39). This is why the published, peer-reviewed Java
extraction tool is used rather than re-implementing the classification.

The injection is a **three-stage hybrid** (flag-gated; see below):

1. **Java extraction (cached, 100%, one run per category).**
   `braunschweig.freight.extraction` runs the published matsim
   application-contrib tool `RunExtractFreightTrips`
   (`ExtractRelevantFreightTrips`) against the dissolved ZGB study-area polygon
   (the union of the in-scope municipalities, plus the cordon buffer when
   `cordon_enabled`) on the German-wide network. The tool routes every freight
   trip, classifies it, trims each plan at the study-area boundary and shifts
   the departure time by the access travel time. The matsim **2025.0-PR3568**
   build writes **no category attribute** on the output persons (the
   `geographical_Trip_Type` attribute only exists in later matsim-libs
   versions -- verified on the real output: all 49 758 extracted trips came
   back `unknown`), so the stage runs the **unmodified tool once per category**
   (`--tripType INTERNAL/INCOMING/OUTGOING/TRANSIT`, ~45 min each) and returns
   `{category: plans_file}` -- the exact published classification, no geometric
   heuristic (trimmed endpoints lie on network nodes *inside* the polygon, so a
   point-in-polygon test cannot recover the category). Further verified CLI
   quirks of this build: the option is `--LegMode` (capital L), there is no
   `--subpopulation` option (it hard-codes `freight`), and plans/network/output
   paths must be absolute (the tool NPEs on a bare `--output` filename). This
   stage is **sampling-rate independent** (cached by synpp), so the expensive
   routing runs once and is reused across sampling rates. The local-only inputs
   are validated up front by `braunschweig.data.freight.german_wide`, which
   fails early with the download command when a file is absent.

2. **Python trips stage.** `braunschweig.freight.trips` parses the four
   per-category plans files with a streaming `xml.etree` reader (deliberately
   **not** matsim-tools: the repo-local `matsim` package shadows the PyPI
   `matsim-tools` import, so the tooling reader is unavailable here), labels
   each trip with its extraction category, rewrites the per-file person ids to
   the collision-free, self-documenting `freight_<category>_<n>` (each
   per-category tool run renumbers from `freight_0`), writes an inspectable
   `freight_trips.gpkg`, and returns one tidy trips DataFrame.

3. **Injection hook.** `braunschweig/matsim/simulation/prepare.py`
   (`_inject_freight`) runs **after** the cordon cut. It Bernoulli-samples the
   trips DataFrame at `freight_sampling_rate` (`None` => `sampling_rate`; seeded
   RNG, offset `+81247`) and writes `freight_trips_sampled.csv`. Sampling the
   freight to the run's sampling rate is **required** because the global qsim
   `flowCapacityFactor` is scaled to the sampling rate -- injecting 100 % freight
   into a 25 % scenario would overload the links. It then runs the Java tool
   `RunInjectFreight`, which builds one freight agent per sampled row
   (subpopulation `freight`, single `truck` leg, vehicle type `heavy_truck`),
   adds `truck` to the car links' allowed modes, adapts the config, and writes
   the population / vehicles / network / config in place.

**Discrete-mode-choice isolation.** Freight agents must not participate in the
person mode choice: `BraunschweigModeAvailability` returns only `{truck}` for
subpopulation `freight`, and a constant-zero `FreightTruckUtilityEstimator` is
bound so the truck leg carries no behavioural utility (freight routes are fixed,
not re-chosen).

**Analysis exclusion.** Injected freight agents are person-trip artefacts, not
synthetic residents, so they are excluded from every person-travel analysis:
`braunschweig.analysis.freight_filter.drop_freight_agents` removes the
`freight_`-prefixed agents at every `eqasim_trips.csv` read (dashboard,
mid_validation, spatial-demand and behaviour tabs). With freight off the filter
is a no-op.

**Assumptions (both configurable, neither calibrated).** Two parameters are
explicit ASSUMPTIONS, not validated references: `freight_truck_pce` (passenger-
car equivalent **3.5**) and `freight_truck_max_velocity_kmh` (**80 km/h**, the
German StVO speed limit for HGVs > 7.5 t). They are exposed as config keys so a
later calibration can override them.

Config keys (registered in `prepare.configure`): `freight_enabled` (default
**true**), `freight_sampling_rate` (default `None` => `sampling_rate`),
`freight_truck_pce` (3.5), `freight_truck_max_velocity_kmh` (80.0); the
extraction stage additionally reads `freight_crs` (default EPSG:25832),
`freight_plans_path` and `freight_network_path` (defaults under
`braunschweig/freight/german-wide-freight-v3/`). The **OFF path**
(`freight_enabled: false`) is byte-identical to the pre-feature pipeline: no
freight stages are requested, no injection runs, and the analysis filter is a
no-op. The committed run configs reflect this -- the two real-data run configs
(`config_local_braunschweig.yml`, `config_server_braunschweig_100pct.yml`) set
`freight_enabled: true`; every other `config_*.yml` (dryrun, smoke, popsim,
intermediate sampling rates) sets `freight_enabled: false` so they never require
the local-only freight inputs.

A possible follow-up (NOT done) is to **calibrate the injected freight against
BASt automatic HGV counts** at the ZGB counting stations (Dauerzaehlstellen), so
the truck volumes on the ZGB motorways are validated against observed counts
rather than taken as-is from the german-wide-freight model.

Tests: `tests/test_download_german_wide_freight.py`,
`tests/test_freight_data.py`, `tests/test_freight_extraction.py`,
`tests/test_freight_trips.py`, `tests/test_freight_injection_wiring.py`,
`tests/test_freight_filter.py`.

## Shared persistent stage-cache (`cache_share`, prime-on-launch)

Expensive **sampling-rate-independent** synpp stages (above all
`braunschweig.freight.extraction` -- the ~3 h published Java routing, run once per
trip category) are recomputed on every fresh run because synpp caches per
`working_directory`. `braunschweig.cache_share` + the `scripts/run_synpp.py`
launcher reuse them across runs/machines by **priming**: synpp stores each stage as
`<module>__<hash>.p` (+ `<module>__<hash>.cache/`) and re-validates `<hash>` on load,
so we never recompute synpp's hash -- we copy the artifacts and let synpp decide.

- `scripts/cache_share.py export --working-directory <wd> --store <store> --modules m1,m2`
  copies a stage's cache artifacts into a shared store.
- `scripts/cache_share.py prime  --working-directory <wd> --store <store> --modules m1,m2 [--recompute m1]`
  copies the store's entries for the requested modules into a target working_directory.
- `run_synpp.py` calls `prime_from_config` BEFORE synpp runs, driven by config keys:
  `cache_share_enabled` (default **true**), `cache_share_store`
  (default `eqasim-data/cache_shared`), `cache_share_stages` (default = the freight
  chain), `cache_share_recompute` (default `[]`; `["*"]` = recompute all).
- `run_synpp.py` calls `export_to_store_from_config` AFTER a **successful** run (a
  failed run raises first, so it never seeds the store), copying `cache_share_stages`
  from the working_directory into the store. Gated by `cache_share_enabled` AND
  `cache_share_export` (default **true**; set false to prime-without-export on a
  throwaway config). The auto-export uses `cache_share.export(..., skip_existing=True)`
  so an entry already in the store is **never overwritten**; a different config/content
  has a different `<hash>` and is stored alongside. The CLI `export` keeps its
  overwrite default (`skip_existing=False`).

A primed entry whose hash does NOT match the target config is **ignored by synpp and
recomputed** -- never a corruption, only a forgone speedup (logged as a miss; no
silent fallback). The store is gitignored and travels via the existing
`scripts/sync_data_to_server.ps1`. **Exclusion:** stages whose hash depends on
machine-variable config (e.g. an auto worker count `num_workers: 0` ->
`cpu_count - 2`) will not hit across machines -- pin a fixed integer there if you need
cross-machine reuse. `cache_share_enabled: false` makes the launcher a pure no-op
(byte-identical to plain `python -m synpp`). Design:
`docs/superpowers/specs/2026-06-22-shared-stage-cache-design.md`. Tests:
`tests/test_cache_share.py`, `tests/test_cache_share_cli.py`,
`tests/test_run_synpp_prime.py`.

**Shareable-stage set + fixed work_dir (Tier A / B).** The two all-features server
configs share, beyond the freight chain, the 32 empirically verified
sampling- AND path-independent stages (identical synpp hash at 1% and 25%) plus
`braunschweig.popsim.stage` and `braunschweig.popsim.completed_donor`. Sharing
`popsim.stage` requires a SINGLE fixed `braunschweig.population.popsim.work_dir`
(`eqasim-data/popsim_work_allfeat`) across all run configs so its hash is identical;
the stale-batch guard (`purge_stale_batches_on_config_change`) keeps a shared
work_dir safe on a config change. The MiD donor build (member completion +
weekend-plan match) is the `braunschweig.popsim.completed_donor` stage: it depends
only on the MiD data, the random seed, the seed day-filter, and the weekend-plan
flag -- NOT on controls / sampling / work_dir -- so it is computed once and reused
across ALL runs (including control-tier changes). Export a completed run's stages to
the store with `python scripts/cache_share.py export ...`; future runs prime them.
Design: `docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md`.

## Run analysis (post-simulation)

The validation notebook `braunschweig/analysis/validation_mid2023.ipynb`
has a runnable counterpart that produces every table, figure and
`report.json` for one eqasim run output directory:

```powershell
python -m braunschweig.analysis.run_mid_validation `
    --output-dir eqasim-data/output_bs_25pct_parking `
    --label "25pct_parking"
```

Outputs land in `<output-dir>/analysis/mid_validation/`:

- `report.json` — headline KPIs (persons, trips, license/employment by
  Kreis, mean commute km vs MiD P13).
- `summary.md` — Markdown digest with three reference-comparison tables.
- `commute_bands_vs_p13.csv`, `commute_mean_vs_p13.csv`,
  `license_vs_p17_1.csv`, `employment_vs_p9.csv`,
  `secondary_success.csv`, `persons_with_kreis.csv` — intermediate
  long-form tables for downstream comparison scripts (e.g. parking-on
  vs. no-parking).
- `01_demographics.png` … `07_employment_rate.png` — figures.

Combined dashboard + MiD validation in one call:

```powershell
python -m braunschweig.analysis.run_full_analysis `
    --output-dir eqasim-data/output_bs_25pct_parking `
    --sim-cache  eqasim-data/cache_bs_25pct_parking `
    --label      "25pct_parking"
```

Tests: `tests/test_run_mid_validation.py` covers the helpers
(`band_share`, `_bool_share`, markdown rendering, CLI parser).

### SimWrapper dashboards

The run analytics can additionally be exported as a self-contained
**SimWrapper** dashboard project (https://simwrapper.app), so the whole
dashboard is viewable inside the MATSim/SimWrapper ecosystem. There are two
complementary, flag-gated layers:

**Layer 1 - MATSim simwrapper contrib (Java).** The `braunschweig` module
(`../eqasim-java-bs`) depends on `org.matsim.contrib:simwrapper` (pinned to the
active MATSim version `2025.0-PR3568`, verified present on `repo.matsim.org`).
`RunSimulation` registers `SimWrapperModule` behind a `--simwrapper`
CommandLine flag, so MATSim writes its standard dashboards (network volumes,
mode share, trips/legs) as `dashboard-*.yaml` into `simulation_output/`. The
pipeline (`matsim/simulation/run.py`) passes `--simwrapper true` only when the
config key `simwrapper_dashboards` is set (**default `False`** -> a standard run
is byte-identical).

**Layer 2 - Python emitter (`braunschweig.analysis.simwrapper`).** Converts the
existing `record` dict from
`braunschweig.analysis.dashboard.build_dashboard.assemble_run_record` (the same
metrics that drive the interactive HTML dashboard - **no scientific logic is
duplicated**) into SimWrapper-native CSV + `dashboard-*.yaml` written to
`<output_dir>/simwrapper/`. It rebuilds the full HTML dashboard as 8 tabs:
Overview (KPI tiles), Mode share (final / commute-vs-MiD P12_1 / iteration
evolution), Distances (commute distribution vs MiD P13 + mean km by mode),
Time of day, Convergence (score + distance evolution), Per-Kreis (table + bar),
OD (matrix table + a real **aggregate-od spider** built from the 8 ZGB Kreis
zones, VG250 EPSG:25832, written as `zones.shp`), and Quality (EMD vs MiD).
Tabs whose source data is absent are skipped with an explicit log line (no
silent fallback). Regenerate standalone:

```powershell
python -m braunschweig.analysis.simwrapper.export `
    --output-dir eqasim-data/output_bs_25pct `
    --sim-cache  eqasim-data/cache_bs_25pct `
    --label      "25pct"
```

It also runs **default-on** inside `run_full_analysis` (disable with
`--no-simwrapper`; it is read-only and writes only into the new `simwrapper/`
subfolder). Open `<output_dir>/simwrapper/` via "View local files" in
simwrapper.app; the Layer-1 MATSim dashboards open from `simulation_output/`.

**Spatial / fleet map tabs (`braunschweig.analysis.simwrapper.spatial_export`).**
On top of the 8 chart/table tabs, four interactive **map** tabs are emitted from
the per-agent geodata (reusing
`braunschweig.analysis.population_validation.population_source.load_population`
and `braunschweig.analysis.spatial` for the VG250 Kreis polygons -- no geo logic
is duplicated): **Fleet** (per-vehicle `xytime` point clouds coloured by engine
power and by BEV status, a per-Kreis BEV-share / mean-power **choropleth** on the
VG250 GeoJSON, and a brand-mix or powertrain-mix bar -- "where are the VW / the
E-vehicles"); **Spatial demand** (`hexagons` density of trip origins &
destinations from `eqasim_trips.csv`); **Socio** (`xytime` home points coloured by
`household_income_eur`); **Behaviour** (`sankey` purpose->mode + a `scatter` of the
per-Kreis car share Sim vs MiD P12). All coordinates are EPSG:25832 for the point
plugins; the choropleth GeoJSON is reprojected to EPSG:4326. Each tab is
**skipped with an explicit log line** when its source columns/files are absent
(e.g. the rich fleet exists only in the all-features run; `eqasim_trips.csv` only
when MATSim has run) -- no silent skips. BEV is identified by the verified real
`powertrain == "bev"` value.

**Commuter (Pendler) tab.** `braunschweig.analysis.simwrapper.commuters` +
`spatial_export.emit_commuters` add an in-/out-/internal-commuter analysis per
Kreis: `commuter_balance` (Einpendler / Auspendler / Binnen / netto, plus the
cross-cordon `einpendler_extern` from the OD "external" zone), `top_relations`
(Kreis->Kreis flows), a per-Kreis **net-balance choropleth**
(`kreis_commuters.geojson`) and an in/out/internal bar. It works in **both
modes**: the work Kreis x Kreis matrix comes from the MATSim realised work OD
(`record["matsim"]["od_matrix"]["work"]`) when MATSim has run, otherwise from
the **synthesis** home->work assignment (`*commutes.gpkg`, classified to Kreise
via VG250) -- the active source is named in the tab title so the two are never
conflated. (`einpendler_extern` is 0 for the synthesis population, which lives
entirely inside ZGB; cross-cordon Einpendler are a separate injection.)

**Automatic pipeline stage + two modes.** `braunschweig.analysis.simwrapper_export`
is a synpp stage that writes `<output_path>/simwrapper/` on **every** run (add it
to a config's `run:` list). It always depends on `synthesis.output`; it
additionally depends on `matsim.simulation.run` ONLY when
`simwrapper_include_matsim: true` (an explicit flag, NOT the global default-True
`run_matsim`, so a synthesis-only pipeline never accidentally forces a MATSim
run). Thus: a **synthesis-only** run writes all synthesis tabs (fleet, socio,
commuters-from-synthesis, ...) and the MATSim tabs skip with a log; a **full**
run additionally writes all MATSim tabs. Flag-gated by `simwrapper_export_enabled`
(default true); it only adds the `simwrapper/` subfolder, so existing run outputs
stay byte-identical. The CLI / stage share one entry point
`braunschweig.analysis.simwrapper.export.export_all(output_dir, sim_cache=None, ...)`
(`sim_cache=None` => synthesis-only).

**Performance.** Raw `xytime` point clouds are down-sampled to `MAX_XYT_POINTS`
(default 150 000) with a fixed seed and an explicit log line (no silent
truncation); aggregate maps (choropleths, hexagon density, commuter balance) use
the full data. The Kreis key normalisation is vectorised. A 1% sample run is the
intended fast end-to-end test vehicle (a fresh 1% pipeline run writes the full
`synthesis.output` the export consumes).

Tests: `tests/test_simwrapper_writers.py`,
`tests/test_simwrapper_export.py` (synthetic `record` fixture per tab + a
real-VG250 OD-spider test exercising the primary geometry path),
`tests/test_simwrapper_spatial.py` (card helpers + the pure
`_trips_xy`/`_purpose_to_mode`/`fleet_by_kreis`/economic-status-ordinal logic +
the commuter integration), `tests/test_simwrapper_commuters.py` (commuter matrix
+ balance + top-relations) and `tests/test_simwrapper_stage.py` (the synpp stage
configure/execute in both modes).

## Language policy

All code must be written in English.

This includes:

1. Class names
2. Method names
3. Variable names
4. Package names
5. File names
6. Comments
7. JavaDoc
8. Log messages
9. Commit messages
10. Configuration descriptions
11. Test names
12. Documentation inside the repository

German may only be used in external text outputs when explicitly requested. Code, comments, and technical documentation must remain English. Chat responses to the user are in German.

## General coding principles

Write code that is:

1. Correct
2. Reproducible
3. Efficient
4. Traceable
5. Easy to review
6. Easy to maintain
7. Scientifically defensible
8. Consistent with the existing project structure

Do not write clever code when clear code is possible.

Do not introduce unnecessary abstraction.

Do not duplicate logic.

Do not silently change behavior.

Do not remove existing functionality unless explicitly requested.

Do not invent data assumptions. If an assumption is required, document it clearly.

## MATSim and eqasim style

Follow MATSim and eqasim conventions where applicable.

Use Java naming conventions:

1. Classes and interfaces use `UpperCamelCase`
2. Methods use `lowerCamelCase`
3. Variables use `lowerCamelCase`
4. Constants use `UPPER_CASE_WITH_UNDERSCORES`
5. Package names use lowercase
6. Abbreviations should be avoided unless they are established domain terms

Use braces consistently, also for single line `if`, `else`, `for`, and `while` blocks.

Prefer readable lines. Lines up to 132 characters are acceptable when this improves readability.

Keep code ASCII only where possible. Avoid non ASCII characters in source code, especially in identifiers and string constants.

## Java version and dependencies

Use the Java version required by the active MATSim version.

Do not add new dependencies unless they are clearly justified.

Before adding a dependency, check whether the same task can be solved with:

1. Standard Java
2. MATSim utilities
3. Existing project utilities
4. Existing eqasim components

Document every new dependency and why it is needed.

## Architecture

Prefer small, focused classes.

Each class should have one clear responsibility.

Separate the following concerns:

1. Input parsing
2. Scenario preparation
3. Configuration
4. Simulation execution
5. Analysis
6. Output writing
7. Validation
8. Visualization preparation

Avoid mixing analysis logic with simulation setup.

Avoid mixing file system logic with domain logic.

Avoid global mutable state.

Use dependency injection where it is already used by MATSim or eqasim.

Keep MATSim modules, config groups, bindings, and analysis components cleanly separated.

## Configuration

All relevant parameters must be configurable.

Do not hard code paths, thresholds, random seeds, scenario names, modes, CRS definitions, or calibration parameters unless there is a strong reason.

Prefer explicit configuration objects over scattered constants.

Every configuration option should have:

1. A clear name
2. A documented meaning
3. A default value where reasonable
4. A unit if applicable
5. A valid range if applicable

Use descriptive names such as:

```java
maximumTransferDistanceMeters
sampleSize
randomSeed
inputPopulationPath
outputDirectory
```

Avoid unclear names such as:

```
x
tmp
value1
param
data
```

## Paths and file handling

Use explicit and reproducible paths.

Do not rely on hidden working directory assumptions.

Validate that required input files exist before processing.

Fail early with clear error messages when inputs are missing or invalid.

Create output directories explicitly.

Never overwrite important outputs silently.

If overwriting is allowed, make it explicit in the configuration or log output.

## Scientific reproducibility

Every simulation or analysis run should be reproducible.

Whenever possible, log:

- Scenario name
- Run identifier
- Git commit hash if available
- MATSim version
- eqasim version if applicable
- Java version
- Random seed
- Config file path
- Input file paths
- Output directory
- Main parameter values
- Start time and end time
- Runtime
- Number of agents, links, facilities, vehicles, carriers, or shipments where relevant

Do not use random processes without an explicit random seed.

If deterministic behavior cannot be guaranteed, document why.

## Data provenance

All generated data must be traceable.

When producing derived files, document:

- Which input files were used
- Which filters were applied
- Which assumptions were made
- Which coordinate reference system was used
- Which aggregation level was used
- Which time period was represented
- Which software step generated the file

Do not create output files with ambiguous names.

Prefer names such as:

```
population_hanover_2025_sample_0.10.xml.gz
carrier_tours_baseline_2025_weekday.csv
network_cleaned_epsg25832.xml.gz
validation_summary_b2b_share_by_zone.csv
```

Avoid names such as:

```
output.csv
final.csv
new_result.csv
test.xml
```

## Documentation

Write concise but useful documentation.

Document public classes and public methods with JavaDoc when their purpose is not obvious.

JavaDoc should explain:

- What the class or method does
- Which assumptions it makes
- Which input data it expects
- Which output it produces
- Which units are used
- Whether the method has side effects

Do not write comments that only repeat the code.

Bad example:

```java
// Set count to zero.
int count = 0;
```

Good example:

```java
// Trips without a valid destination are excluded because they cannot be assigned to a network route.
```

## Logging

Use structured and meaningful logging.

Prefer log messages that help with debugging and scientific traceability.

Log important processing steps, not every minor operation.

Use appropriate log levels:

- `info` for major processing steps
- `warn` for recoverable problems or assumptions
- `error` for failures
- `debug` for detailed diagnostics

Do not use `System.out.println` in production code.

## Error handling

Fail early when input data is invalid.

Use clear exception messages.

Exception messages should explain:

- What failed
- Which input caused the problem
- Why the value is invalid
- How the issue can be fixed if this is obvious

Do not swallow exceptions silently.

Do not catch broad exceptions unless there is a clear reason.

Do not continue after invalid input if this can compromise scientific results.

## Validation and quality control

Every relevant processing step should include plausibility checks.

Add validation for:

- Missing files
- Empty datasets
- Invalid coordinates
- Invalid CRS definitions
- Negative travel times
- Negative distances
- Negative demand values
- Invalid mode names
- Invalid activity types
- Invalid vehicle capacities
- Invalid carrier or shipment identifiers
- Inconsistent totals after aggregation
- Unexpected changes in population size
- Unexpected changes in demand totals

For scientific workflows, report validation results in a dedicated summary file where appropriate.

## Fallback transparency (no silent fallbacks) — MANDATORY

Silent fallbacks are a recurring source of hidden bugs in this project: a stage's
primary (real-data / proper) method quietly fails for some or all items and a
fallback catches it, so the pipeline runs and the tests stay green — but the
intended method never actually worked. Bugs then go undetected because "it ran".
This is unacceptable for research software.

Therefore, for **every** code path that has a fallback (nearest-neighbour fill,
whole-region pool, "rda"/"random" solver fallback, scalar-default-when-map-missing,
default-when-data-absent, except/try recovery, etc.):

1. **Make the fallback observable.** Count and `log` (info/warn) how many items used
   the PRIMARY method vs the FALLBACK, as an explicit rate, e.g.
   `"[stage] primary 9842/10000 (98.4%), fallback 158 (1.6%)"`. Never let a fallback
   fire silently.
2. **Treat a high fallback rate as a failure signal.** If most/all items hit the
   fallback (e.g. above a configurable threshold, and especially ~100%), that almost
   always means the primary method is broken (a format mismatch, an empty join, a
   wrong key) — surface it loudly (`warn`, or `raise` where a high rate cannot be
   scientifically defensible). Add the rate to the per-run validation summary where
   one exists.
3. **Test the primary method, not just the fallback.** A green test that only
   exercises (or silently tolerates) the fallback proves nothing about the real
   method. Add tests/assertions that the PRIMARY path is actually taken on
   representative input, and that the fallback rate stays below an expected bound.
4. **When adding or reviewing ANY stage with a fallback, verify primary-method
   coverage** and wire in the rate logging above. Do this proactively across the
   model, not only when a bug appears.

This applies to existing fallbacks too: when you touch a stage, add the rate
instrumentation if it is missing.

## No invented reference values; convergence is not validation — MANDATORY

Two related failure modes are strictly forbidden because they silently fabricate
scientific claims:

1. **Never invent or assert "target" / "reference" / "ground-truth" values.**
   A reference value (a modal split, a mean distance, a rate to compare against)
   may only be stated if it is traceable to a committed source in the repo (a
   pinned CSV under `eqasim-data/.../`, a documented table in CLAUDE.md, a cited
   external publication with the figure). If no such source exists, say so
   explicitly and label the number as an **assumption** ("ASSUMPTION: ...", with
   the reasoning) -- never as an established target. Do not carry numbers from
   chat / prompt context into a results report as if they were validated
   references. Comparing model output to a made-up target and calling the fit
   "excellent" is a fabricated result and is unacceptable.

2. **Convergence (stability) is NOT the same as validation (matching reality).**
   The eqasim mode-share termination criterion (`eqasim:termination`,
   `ModeShareTracker`) stops the MATSim run when the modal split **stops changing**
   between iterations (smoothed change `< threshold`, default 0.001) -- it has
   **no real-world reference shares** and says **nothing** about whether the
   equilibrium matches observed travel behaviour. Report it precisely: "the run
   converged (mode shares stabilised, change below threshold)". Never phrase a
   stabilised equilibrium as "hit the target" / "calibrated to the data" unless
   the realised shares were actually compared to a committed observed reference.

When unsure whether a number is a real reference or an assumption, treat it as an
assumption and flag it. Cautious, honest, traceable reporting always wins over a
confident-sounding but unsupported claim (see "Research reporting", "Do not
overstate results").

## Tests

Add tests for non trivial logic.

Prefer small unit tests for:

- Data transformations
- Filtering rules
- Assignment logic
- Cost calculations
- Aggregation logic
- Routing helper logic
- Validation checks

Use integration tests when testing MATSim scenario setup or full pipeline behavior.

Tests must be deterministic.

Use small synthetic test data where possible.

Do not rely on large external datasets in unit tests.

## Performance

Efficiency matters, but correctness comes first.

Avoid unnecessary nested loops over large MATSim populations, links, events, carriers, or shipments.

Use maps, sets, indexes, and spatial indexes where appropriate.

Avoid repeated file reads.

Avoid repeated route calculations when caching is safe.

Document caching behavior clearly.

Be careful with memory usage when processing large event files, populations, networks, or freight scenarios.

Prefer streaming approaches for large files when possible.

## MATSim specific rules

Use MATSim APIs instead of manually parsing MATSim XML files unless there is a clear reason.

Keep MATSim config handling explicit and reproducible.

Do not silently modify MATSim config values.

When modifying a MATSim scenario, clearly separate:

1. Config creation
2. Scenario loading
3. Scenario modification
4. Controller setup
5. Module installation
6. Simulation execution
7. Post processing

Use established MATSim concepts correctly:

- Scenario
- Config
- Controler
- Population
- Network
- ActivityFacility
- Vehicle
- Carrier
- Plan
- Leg
- Activity
- Events

Do not create custom replacements for standard MATSim functionality unless required.

## eqasim specific rules

Follow the pipeline logic and structure used by eqasim where applicable.

Keep scenario generation, population synthesis, simulation setup, and analysis steps modular.

Prefer reproducible pipeline stages over one off scripts.

If adapting logic from eqasim examples, keep the structure understandable and document the adaptation.

Do not copy code blindly. Adapt it to the project context and explain relevant assumptions.

## Geospatial processing

Always document the coordinate reference system.

Use metric projected coordinate systems for distance based calculations.

Do not calculate metric distances in WGS84 longitude and latitude coordinates.

Validate geometry validity before spatial operations where relevant.

Document buffer distances and spatial thresholds in meters.

Use explicit names for spatial thresholds, for example:

```
maximumStopAccessDistanceMeters
transferSearchRadiusMeters
zoneAssignmentBufferMeters
```

## Units

Always make units explicit in variable names, method names, documentation, and output column names.

Examples:

```
travelTimeSeconds
distanceMeters
speedMetersPerSecond
emissionsGrams
costEuro
durationHours
```

Avoid ambiguous names such as:

```
time
distance
speed
cost
```

## Output tables

Output tables must be readable, documented, and stable.

Column names should be explicit and consistent.

Use snake case for CSV column names.

Examples:

```
person_id
tour_id
carrier_id
vehicle_id
departure_time_seconds
travel_time_seconds
distance_meters
co2_grams
cost_euro
```

Do not mix naming styles in one file.

Do not rename columns without updating downstream code and documentation.

## Research reporting

When generating analysis outputs, include enough information to reproduce the result.

Every figure or table should be traceable to:

- Input data
- Scenario
- Processing script
- Parameters
- Date or run identifier

Do not overstate results.

Use cautious scientific language.

Report limitations where relevant.

Distinguish clearly between observed data, modeled data, assumptions, and derived indicators.

## Code changes

Before changing code, understand the existing structure.

Make minimal necessary changes.

Preserve existing behavior unless a behavior change is explicitly requested.

When making a change, consider:

- Does this break reproducibility?
- Does this change scientific results?
- Does this affect previous outputs?
- Does this require a test?
- Does this require documentation?
- Does this require a configuration option?

## Refactoring

Refactoring is allowed when it improves clarity, maintainability, or performance.

Do not refactor large unrelated parts of the codebase while solving a specific issue.

Keep refactoring behavior preserving unless explicitly requested.

If behavior changes, document the change clearly.

## Comments and JavaDoc style

Comments must be written in English.

Use comments to explain why something is done, not only what is done.

Prefer precise technical language.

Avoid vague comments such as:

```java
// Handle data.
```

Prefer specific comments such as:

```java
// Remove shipments without valid zone assignment because they cannot be assigned to a carrier service area.
```

## Naming examples

Use descriptive names:

```
CarrierDemandReader
PopulationValidationWriter
NetworkModeCleaner
FreightScenarioBuilder
TourDistanceAnalyzer
```

Avoid vague names:

```
Helper
Utils
Processor
Manager
Stuff
NewClass
```

Utility classes are acceptable only when they contain cohesive static helper methods.

## Git and version control

**Never run `git push` without explicit user confirmation.** Committing locally is
fine and expected, but every push to any remote must be approved by the user
first: ask before pushing and wait for an explicit "yes" / "push it" / equivalent
each time (a prior confirmation does not authorise later pushes). This applies to
all branches and remotes, including `origin/main`.

Keep commits focused.

Commit messages must be in English.

A good commit message explains the change and its purpose.

Examples:

```
Add validation for missing freight carrier capacities
Refactor zone based transport cost caching
Fix CRS handling in stop access distance calculation
Document baseline scenario configuration
```

Do not use unclear commit messages such as:

```
fix
update
changes
final
new stuff
```

## Review checklist

Before considering a task complete, check:

- Does the code compile?
- Are all names in English?
- Are comments and JavaDoc in English?
- Are units explicit?
- Are assumptions documented?
- Are paths configurable?
- Are random seeds controlled?
- Are outputs traceable?
- Are input files validated?
- Are relevant tests added or updated?
- Are logs useful?
- Is the code consistent with MATSim and eqasim style?
- Is the solution efficient enough for large simulation datasets?
- Is the result scientifically defensible?
- Is the documentation sufficient for another researcher to understand the workflow?

## Preferred response behavior for Claude

When modifying code, first inspect the surrounding code and project structure.

Do not guess APIs if the relevant code can be inspected.

Do not invent missing classes, methods, or dependencies.

If information is missing, state the uncertainty clearly.

When suggesting changes, explain the reason briefly.

When producing code, provide complete and consistent code, not isolated fragments, unless a fragment is explicitly requested.

When a task affects scientific results, explicitly state whether the change may alter outputs.

When implementing performance improvements, explain the expected performance benefit and any trade off.

## Non negotiable rules

- All code and comments must be in English.
- All scientific assumptions must be explicit.
- All relevant parameters must be configurable.
- All output must be traceable.
- All non trivial logic must be documented.
- All important processing steps must be logged.
- All input data must be validated.
- All changes must preserve scientific credibility.
- Completeness, consistency, reproducibility, and clarity are mandatory.
