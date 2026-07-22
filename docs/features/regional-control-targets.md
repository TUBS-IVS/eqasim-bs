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
KREIS controls (household AND, since 2026-07-08, person level). The registry lives in
`braunschweig/popsim/kreis_attribute_control.py` (`REGISTRY`, `load_kreis_target`,
`attribute_kreis_count_table`) and currently declares five entries:

| control | level | tier | seed column (MiD) | target CSV | config toggle | default |
|---|---|---|---|---|---|---|
| `economic_status` | household | hard | `oek_status` (raw, `== k`) | `target2026_economic_status_by_kreis.csv` | `braunschweig.population.popsim.status_kreis_control` | on |
| `number_of_cars` | household | hard | `number_of_cars` (resolved from `H_ANZAUTO`) | `target2026_number_of_cars_by_kreis.csv` | `braunschweig.population.popsim.number_of_cars_kreis_control` | on |
| `number_of_bicycles` | household | soft | `number_of_bicycles` (resolved from `anzpedrad`, bicycles INCL. pedelecs) | `target2026_number_of_bicycles_by_kreis.csv` | `braunschweig.population.popsim.number_of_bicycles_kreis_control` | on |
| `has_ebike` | household | soft | `has_ebike` (0/1, resolved from `H_ANZPED`) | `target2026_has_ebike_by_kreis.csv` | `braunschweig.population.popsim.has_ebike_kreis_control` | on |
| `trip_class` | person | soft | `trip_class` (int 0..3, resolved from `anzwege1` via `map_trip_class`) | `target2026_trip_class_by_kreis.csv` | `braunschweig.population.popsim.trip_class_kreis_control` | on |

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

**`trip_class` -- the first person-level entry (2026-07-08).** Every entry above partitions a
per-Kreis HOUSEHOLD total. `trip_class` steers the region-specific travel-behaviour character
instead -- how many trips a person makes on the reporting day (classes `0` / `1-2` / `3-4` /
`5+`) -- at the population INPUT, because end-of-pipe validation cannot change the input
(project decision 2026-07-08). Purpose-built target: `target2026_trip_class_by_kreis.csv`,
produced by `scripts/build_trip_class_target.py` from the COMMITTED SrV aggregate
`srv2023_trip_classes_by_kreis.csv` only (no raw microdata, no MiD blending); the four
`trips_*` share columns are renormalised over the four classes (`share_trips_invalid`
dropped) and, like the other four entries, consumed with `prior_n = 0` (FINAL target). Seed
column: the person-level MiD column `trip_class` (int codes 0..3), derived by
`attributes.map_trip_class` from `anzwege1` (Anzahl Wege am Stichtag). The missing codes 803
(trip module not covered, no diary) and 804 (rueckwirkende Wegeerhebung only) are IMPUTED
within the age band `alter_gr1` using a seeded RNG -- never dropped, because diary
non-response correlates with mobility. The mapper is applied on both seed paths
(`mid.load_mid_seed` and `mid.project_completed_seed`, stage key
`braunschweig.population.popsim.trip_class_kreis_control`); on the completed-donor path a
mirror-imputed household member inherits the mirror donor's diary trip count.

*Person-level mechanics.* Unlike the four household entries, which partition the per-Kreis
household total, the `trip_class` count table partitions the per-Kreis PERSON total (the sum
of the 18 age-x-sex 100m band census columns for that Kreis).

*Three documented decisions* (from `docs/superpowers/plans/2026-07-08-trip-class-kreis-control.md`,
Global Constraints -- also recorded verbatim in the target CSV header):
1. **ASSUMPTION (universe).** The target is built from the SrV Di-Do mittlerer Werktag
   universe, while the MiD seed universe is `kernwo` (1,2,3) = Mo-Fr. The measured difference
   between MiD trip-class shares Mo-Fr vs. Di-Do is <= 0.63 pp per class (2026-07-08,
   `P_GEW`-weighted) -- immaterial, no correction applied.
2. **DECISION (level anchoring).** MiD and SrV measure mobility differently: a uniform
   ~+5..+8 pp immobile-share offset appears across ALL Kreise, i.e. it is a survey-method
   effect, not a regional difference. Measured under the structural controls (economic
   status, cars, bicycles, e-bike): MiD immobile share 16.6-19.0% vs. SrV 10.0-12.2% across
   Kreise. This control deliberately anchors the synthetic trip-class distribution to the
   SrV level, per project decision (regional survey = regional behaviour authority), rather
   than correcting to the MiD level. Consumers of MiD-anchored trip statistics (e.g. tour or
   activity-chain analyses seeded from MiD) must be aware totals shift accordingly.
3. **ASSUMPTION (Wolfsburg, MiD-P36.1 pattern transfer).** ARS 03103 is not covered by the
   SrV Braunschweig+RGB survey. Its `trips_0` share is the SrV region total scaled by
   Wolfsburg's RELATIVE immobility in the MiD 2023 regional Aufstockung (committed
   `mid2023_P36_1.csv`: `nicht_mobil` WOB 21% vs ZGB-Gesamt 19% -> ratio ~1.105); the three
   mobile classes are rescaled proportionally from the SrV region total so the row sums to 1.
   This keeps the SrV LEVEL anchoring while injecting Wolfsburg's MiD-measured relative
   deviation (WOB is the region's most-immobile city per MiD). ASSUMPTION: the WOB-vs-region
   immobility ratio transfers across survey methods and day universes (MiD P36.1 = all
   reporting days; SrV = Di-Do).

*Evidence for adding this control.* The 2026-07-08 S2-A proxy gate measured that the four
STRUCTURAL controls (`economic_status`, `number_of_cars`, `number_of_bicycles`, `has_ebike`)
do NOT move the trips axis: mean SRMSE against the trip-class reference stayed ~0.17 across
all tested arms. Regional travel behaviour needs its own dedicated control; that null result
is what motivated building `trip_class`.

*Honest limits.* Fitting `trip_class` to its own SrV target is not an independent
validation of the resulting travel-behaviour distribution -- it is calibration, not
verification, per the "convergence is not validation" rule above. The D-Ticket and driving-licence
axes remain validation-only (not controls), for the same measurement-artifact reasons given
above for `economic_status`.

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
end-to-end smoke test of the `popsim_mid` stage with all five controls (including
`trip_class`) active (still pending a server run; the unit/integration test suite is green
locally).

## Placement income L2 (#108, MERGED — ADR-0069)

`braunschweig.population.popsim.placement_income` (default ON, PR #212 merged
2026-07-18) replaces the post-hoc income redraw (`income_kreis_control`) with
an own-donor mechanism: each synthesized household keeps a seeded
within-own-bracket draw of its OWN MiD donor's income, and the per-Kreis INKAR
relativity is instead APPROACHED by permuting which real donors sit in which
Kreis, strictly inside exact control-signature groups. When ON it overrides
BOTH `income_kreis_control` and `income_spatial_tilt` (logged); OFF is
byte-identical to the pre-L2 path. A 2-Kreis OFF/ON gate (Salzgitter 03102 +
Wolfsburg 03103, 1%, real data) found invariants unchanged (max|delta|=0),
income<->car-ownership coherence improved within (Kreis, status)
(0.174 -> 0.364), and an honest attainment trade: the redraw hit the INKAR
mean more exactly (+0.8%/+0.5%) while placement only approaches it
(+4.7%/-3.1%, 52% of households have no signature-preserving freedom to move).
Entry points: `braunschweig/popsim/placement_income.py`, `popsim/stage.py`;
gate harness `scripts/gate_placement_income.py`. See ADR-0069 in
`docs/DECISIONS.md` and spec `docs/superpowers/specs/2026-07-04-income-
weighted-household-placement-design.md`.

## SrV-anchored trip-participation controls (#224, MERGED — PR #225)

Four hard, person-level per-Kreis `KreisAttributeControl` entries pull the
MiD-donor population toward the SrV 2023 regional travel-PARTICIPATION levels
(the MiD<->SrV gap is participation, not trip length -- distances already
match MiD, mode is re-simulated in MATSim): the existing `trip_class` control
promoted from soft to hard (pins the Mobilitaetsquote via `trips_0`), plus
three new controls -- `work_participation`, `leisure_participation`,
`education_participation` -- each derived via `attributes.map_participation`
+ `mid.derive_participation_seed` (803/804 diary non-response imputed within
`alter_gr1`, same pattern as `trip_class`). Wolfsburg (not covered by SrV)
uses the SrV region total. Flag-gated, default ON, OFF path byte-identical
(verified).

Felix control-smoke (Kreis 03101, ~252k persons, all four controls hard):
PopulationSim converges (97.2% integerizer optimal, 2.8% infeasible -- not
over-constrained); N_eff cost 0.86% -> 0.62%. Realised vs SrV target, ON vs
OFF (percentage-point gap closed): leisure 22.8 -> 37.7 vs target 41.8
(78% closed), education 9.3 -> 15.8 vs 17.6 (78%), mobility 78.0 -> 84.4 vs
90.0 (53%), work 29.0 -> 30.6 vs 34.4 (30%). An importance sweep (2000 ->
20000, 10x) showed the residual gap is FLAT under reweighting -- it is
donor/feasibility-bound (work-trip-havers are demographically skewed: more
full-time/male, less home-office, so raising their weight would break the
hard demographic margins), not a weighting problem; full attainment needs a
donor-side fix (SrV-based trip chains), tracked as a follow-up. ~5-8pp of the
mobility gap is a documented SrV-vs-MiD survey-method offset (see `trip_class`
above). Entry points: `braunschweig/popsim/kreis_attribute_control.py`,
`data/mid/attributes.py` (`map_participation`), analysis
`analysis/population_validation/participation_fit.py`.
