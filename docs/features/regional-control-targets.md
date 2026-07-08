# Blended regional control targets (MiD x SrV 2023 x LSN)

Per-Kreis PopulationSim control targets that combine, per attribute AND per
Kreis, the most defensible of three sources:

- **MiD 2023 Grossraum Braunschweig** regional tables (all 8 ZGB Kreise,
  committed under `eqasim-data/data/braunschweig/mid/`),
- **SrV 2023 "Braunschweig und RGB"** aggregates (7 Kreise, NO Wolfsburg;
  committed under `.../srv/`; stratified PSU design over ~44 selected
  municipalities -> per-Kreis rows are assumption-grade),
- **LSN income-tax register** (A9170102) as a full-count ORDERING arbiter
  (`.../lsn/lsn2022_income_tax_by_kreis.csv`; taxable income != net income,
  levels are never used).

Decision rule per Kreis (`braunschweig/popsim/blended_targets.py`, applied by
`scripts/build_blended_kreis_targets.py`): agreement within 5 pp per
category -> precision-weighted blend; disagreement with an arbiter -> the
survey whose Kreis rank matches the register rank better; disagreement
without arbiter -> MiD shrunk toward the region aggregate (lambda 0.3);
Wolfsburg and Gesamt always MiD. Every output row carries `source` and
`n_effective`.

Outputs (committed, `eqasim-data/data/braunschweig/targets/`):
`target2026_{economic_status,number_of_cars,has_ebike,number_of_bicycles}_by_kreis.csv`.
They are FINAL targets: the `kreis_attribute_control` registry must consume
them with `prior_n = 0`.

## Weights

All SrV aggregates use `GEWICHT_HH_ZENSUS` / `GEWICHT_P_ZENSUS` ("fuer
stadtuebergreifende Auswertungen"), the full expansion to Zensus 2022 counts
per municipality -- not the stratum-internal Standard weights
(`GEWICHT_HH`/`GEWICHT_P`, "fuer Standardauswertungen"). The Standard weights
are normalized to mean ~1 WITHIN each `ST_CODE` stratum, so any cross-stratum
aggregate (every `total` and per-Kreis row, and per-municipality rows within
the two "kleinstaedtisch-doerflich" strata) weights strata by SAMPLE share
instead of population share, while the true expansion factor varies 18x-70x
across strata. This was discovered and fixed 2026-07-08, after the initial
tables shipped with the Standard weights (see `docs/DECISIONS.md`). Both
ZENSUS columns carry zero negative/NaN values in this delivery; the
missing-code filter and drop-rate logging are kept as a defensive guard per
CLAUDE.md "No silent fallbacks".

Key facts feeding the rules (2026-07-08 analysis):
- The MiD H4 Salzgitter status cell (42% high, n_weighted 167) is contradicted
  by BOTH the SrV rebuild (24.3% high) and the LSN register (SZ = poorest ZGB
  Kreis, -19% mean GdE vs NDS) -> SZ resolves to `srv_arbitrated`.
- The economic-status construct is rebuilt on SrV exactly per the MiD handbook
  matrix (`mid2023_economic_status_matrix.csv`, extracted from the handbook
  PDF vector fills; weighted size 1.0/+0.5/+0.3).
- Driving licence and PT/D-Ticket are deliberately NOT targets: the licence
  gap is an established between-survey measurement artifact (MiD self-report
  base, mode-dependent by up to 14 pp; SrV household-reported, 0% missing).

Follow-ups (separate plans): point the S1a registry entries at these targets
(after the S1a branch merge), S2-A proxy evidence gate per target variant,
S3 subset optimizer, SrV axes in the population-validation stage. Trip-class
person-level tables (`srv2023_trip_classes_by_kreis.csv` /
`_by_age.csv`, Task 6, same branch) are SrV-only candidate targets for trip
generation, not blendable with MiD until a workday-matched MiD P39 extraction
exists.

## Wiring into the popsim controls (registry)

The blended `target2026_*` tables above are consumed by `popsim_mid` as registry-driven
KREIS household controls. The registry lives in
`braunschweig/popsim/kreis_attribute_control.py` (`REGISTRY`, `load_kreis_target`,
`attribute_kreis_count_table`) and currently declares four entries:

| control | tier | seed column (MiD) | target CSV | config toggle | default |
|---|---|---|---|---|---|
| `economic_status` | hard | `oek_status` (raw, `== k`) | `target2026_economic_status_by_kreis.csv` | `braunschweig.population.popsim.status_kreis_control` | on |
| `number_of_cars` | hard | `number_of_cars` (resolved from `H_ANZAUTO`) | `target2026_number_of_cars_by_kreis.csv` | `braunschweig.population.popsim.number_of_cars_kreis_control` | on |
| `number_of_bicycles` | soft | `number_of_bicycles` (resolved from `anzpedrad`, bicycles INCL. pedelecs) | `target2026_number_of_bicycles_by_kreis.csv` | `braunschweig.population.popsim.number_of_bicycles_kreis_control` | on |
| `has_ebike` | soft | `has_ebike` (0/1, resolved from `H_ANZPED`) | `target2026_has_ebike_by_kreis.csv` | `braunschweig.population.popsim.has_ebike_kreis_control` | on |

`economic_status` was switched from the raw MiD H4 CSV (the old
`mid2023_H4_status_by_kreis.csv` loader) to the blended
`target2026_economic_status_by_kreis.csv` -- the table that already applies the
register-arbitration decision for Salzgitter and the other Kreise above. It is consumed
with `prior_n = 0` (no further Dirichlet shrinkage toward the region aggregate), because
the blended targets are FINAL per the CONSUMER NOTE in each CSV header. The three new
entries (`number_of_cars`, `number_of_bicycles`, `has_ebike`) always use `prior_n = 0` for
the same reason; only `economic_status` still exposes a configurable shrinkage prior
(`braunschweig.population.popsim.status_kreis_shrinkage_n`), kept for backward
compatibility with the pre-blend behaviour.

**Seed-column subtlety.** `economic_status` uses the raw `oek_status` MiD column with
exact `== k` category predicates -- a missing/non-response code simply matches no
category and is excluded, which is correct for an equality test. `number_of_cars` and
`number_of_bicycles`, however, use the *resolved* seed column (`attributes.map_number_of_cars`
/ `attributes.map_number_of_bicycles`, which impute the MiD missing code 99 within the
household-size group `hhgr_gr`, falling back to the global pool), because their top
category is a *range* predicate (`3plus: >= 3` / `4plus: >= 4`). On the raw column, an unresolved
code 99 would land in the top range category by construction and bias the fitted control
toward large fleets; resolving first removes that artifact.

**`number_of_bicycles` construct fix (2026-07-08).** The target CSV
(`target2026_number_of_bicycles_by_kreis.csv`) counts bicycles INCLUDING pedelecs/e-bikes
(MiD codebook table H12.3 "Anzahl Fahrraeder/Pedelecs/E-Bikes im Haushalt"; the matching
SrV side is `E_ANZ_RAD_ALLE_6` "alle Raeder", `srv2023_bikes_incl_ebikes_by_kreis.csv`). The
seed derivation originally resolved `number_of_bicycles` from `H_ANZRAD`, which EXCLUDES
pedelecs -- a construct mismatch against the incl-pedelec target, verified against the
server MiD B1 microdata (218,039 valid rows) to systematically understate ownership (~31 %
of households own >= 1 pedelec). The fix: `attributes.map_number_of_bicycles` now resolves
from the MiD-provided combined column `anzpedrad`, verified to equal
`min(H_ANZRAD + H_ANZPED, 10)` on every valid row (0 mismatches; the 99 missing code
propagates). `H_ANZRAD` is retained on the donor frames for any consumer that still needs
the exclusive count.

**Two seed paths, both wired.** The pipeline has two ways to build the PopulationSim
seed: the raw-seed path (`mid.load_mid_seed`, `complete_members=False`, reads the MiD CSVs
directly) and the completed-donor path (`mid.project_completed_seed`,
`complete_members=True`, the pipeline default). Both derive the
`number_of_cars`/`number_of_bicycles`/`has_ebike` seed columns from the donor's raw
`H_ANZAUTO`/`anzpedrad`/`H_ANZPED` columns, using a seeded RNG (`random_seed + 24680`,
disjoint from the other imputation streams) for the group-wise 99-code imputation. Each of
the four controls is individually toggleable via its config key; with all four toggles off
the stage output is byte-identical to before this feature.

**`has_ebike` now defaults on -- server verification resolved (2026-07-08).** The MiD
household e-bike column was identified and verified against the server MiD B1 microdata:
`H_ANZPED` (Anzahl Pedelecs; values 0..10, missing code 99, the same code schema as
`H_ANZAUTO`/`H_ANZRAD`). `braunschweig.population.popsim.ebike_seed_column` now defaults to
`H_ANZPED` (still configurable, in case a future MiD delivery renames the column), and both
seed paths (`mid.load_mid_seed` and `mid.project_completed_seed`) derive `has_ebike` via
`attributes.map_has_ebike`. `has_ebike` is written onto the persons frame in
`assembly.map_mid_person_attributes` (alongside `number_of_cars`/`number_of_bicycles`) so
the control is measurable against the realized population, not just derivable on the seed.
The remaining documented assumption: MiD "Pedelec" is treated as equivalent to SrV "E-Rad"
(the SrV construct may additionally include S-Pedelecs); this is a minor construct edge
case, not expected to materially bias the control. Issue #116 is resolved.

**Provenance and validation caveats carry over from the target tables.** As documented
above, the SrV inputs feeding these targets are assumption-grade PSU estimates from a
stratified design over a subset of municipalities, and Wolfsburg (03103) and the region
aggregate always fall back to MiD. These are calibration/control targets for
PopulationSim, not an independent validation reference: PopulationSim fitting a control to
its own target says nothing about whether the fitted population matches reality on any
other axis, and convergence of the IPF fit is not the same as validation.

**Remaining follow-ups:** the S2-A proxy evidence gate (comparing the current-set vs.
switched-set targets), a decision on down-weighting the assumption-grade SrV rows by
measured uncertainty (not by preference), an S2-A validation refresh, and a 1-Kreis / 1%
end-to-end smoke test of the `popsim_mid` stage with all four controls active (still
pending a server run; the unit/integration test suite is green locally).
