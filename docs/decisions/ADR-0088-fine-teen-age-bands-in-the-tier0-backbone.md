# ADR-0088 · 2026-08-18 · The tier0 backbone splits the 10-19 age band at the published Zensus bin edges (issue #320)

- **Status:** active
- **Context:** The PopulationSim backbone controls age x sex in nine TEN-YEAR bands
  (`braunschweig.popsim.control_spec.AGE_BANDS`). Inside a band the composition is
  unconstrained: "7 men aged 10-19" is satisfied by any age mix, and since `popsim_mid`
  replicates whole donor households, the mix that best serves the other controls (household
  size, cars, income, employment) wins. Measured on the 100 % population of run
  `synth-100pct-2.2.0-2026-07-23` against DESTATIS 12411-0018 (issue #307, manifest
  `docs/runs/i307-license-pt-measure-2026-08-18.yml`): the 10-19 band TOTAL is fine
  (107,580 vs 104,858, +2.6 %) while its content is not — **15-17 is 51,435 vs 31,430
  (+64 %) and 18-19 is 5,297 vs 21,582 (−75 %)**, with single-year ages 18 (3,829) and 19
  (1,468) nearly empty next to age 20 (17,512). No existing check sees this: the backbone
  aggregates the band, and the validation layer's `age_group` control is coarser still
  (`AGE_GROUP_BOUNDS` = 15/30/45/60/75 puts 15-17 and 18-19 in ONE band).
  The defect is not cosmetic: age enters the mode-choice utility, the education demand, the
  licence gate (18) and every age-restricted control universe, so the 17+/18+ licence
  figures of #307 are themselves distorted by it.
- **Reference question, settled by measurement:** the fix needs a reference finer than ten
  years. The cell parquet's single-year columns (`M_AGE_0..100` / `F_AGE_*`) looked like a
  smooth disaggregation because they are fractional, which would have made them an invented
  reference. They are not:
  1. they sum to `POP_TOTAL_100m_adj` **per cell** to 2.6e-5 (national 82,716,897.4 on both
     sides);
  2. aggregated over the ZGB-8 into the 17 DESTATIS 12411-0018 classes they agree to a
     **mean |Δ| of 0.259 pp** (max 1.233 pp at 50-54); for the two classes at issue,
     15-17 = 30,414 vs 31,430 and 18-19 = 20,839 vs 21,582;
  3. the method (`cleancensus/ages_stage.py`, `docs/AGES_GATE_REPORT.md`) fits THREE
     overlapping published bin systems simultaneously — INFR (incl. `a16bis18`, `a19bis24`),
     ten-year groups, and the 5 classes (incl. `Unter18`) — IPF-raked against the national
     single-year age vector, then downscales 10km → 1km → 100m with HARD row (cell total)
     and HARD column (parent per-age) margins, deterministically;
  4. `M_AGE_10_19_agg` equals the sum of the single-year columns 10..19 **bit-for-bit** in
     all 3,148,482 cells (both sexes).
  Provenance and these figures are recorded in `docs/registry/data/zensus2022_grid_cells.yml`.
- **Decision:**
  1. **Replace the 10-19 band by 10-15 / 16-17 / 18-19** per sex in the tier0 backbone:
     22 age x sex controls at 100 m instead of 18, so 23 controls at 100 m and 24 in the
     catalog. The two new edges coincide with published bins (5-class `Unter18`, INFR
     `a16bis18`), so the targets do not rest on the national-profile interpolation alone.
  2. **Replace, do not add.** The three bands sum to the old one exactly (point 4 above), so
     keeping `10_19` alongside would re-introduce precisely the derivable redundancy the
     tier0 lossless reduction removed, and the 100m → 1km nesting guarantee is unaffected.
  3. **Targets come from the single-year columns.** The ten-year bands exist as precomputed
     `_agg` columns in the parquet and stay single-source identities; the three new bands
     declare `census_source` = their single-year columns and are row-summed by
     `prepared_cells.add_aggregated_controls`, which already carries the missing-source
     warning and the all-sources-missing hard error (so a permanently-zero control cannot
     slip through, issues #149/#150).
  4. **Flag-gated, default ON**: `braunschweig.population.popsim.fine_teen_age_bands`
     (`"on"`/`"off"`, declared in `configs/base_bs.yml`). OFF reproduces the pre-#320
     control set byte-identically — pinned by `tests/fixtures/prep3_controls_baseline.csv`,
     which is now explicitly the flag-OFF baseline. The OFF path also makes the A/B that
     #320's acceptance criteria require possible.
  5. **The flag must reach four places or the run breaks.** The controls frame
     (`build_controls_df`), the parquet column selection (`build_source_columns`), the
     aggregation map (`build_aggregation_map`) AND the per-Kreis person total
     (`person_band_census_columns` / `person_total_by_kreis`) all derive from the band set.
     Threading it revealed a would-be defect: `person_band_census_columns` defaulted to the
     ON column set, so a run with the flag OFF plus an active person-level KREIS control
     (e.g. `trip_class`) would have raised "band columns absent". A regression test now pins
     that a variant mismatch raises rather than summing a partial set.
  6. **50-54 is deliberately NOT split yet.** Of its +1.92 pp synthetic deviation against
     DESTATIS, +1.23 pp is already present in the control INPUT (point 2 above), so the
     residual fit error is roughly +0.7 pp and a split would partly chase the vintage
     difference between Zensus 2022-05-15 and the DESTATIS Fortschreibung. Re-assess after
     the 10-19 split is measured.
- **Rejected alternatives:**
  - *Full DESTATIS resolution (17 classes, 34 controls).* Four times the added constraints
    for bands that are not broken. Every extra control is another target on a small
    fractional per-cell count (a cell with 3 teenagers gets targets like 1.4 / 0.6 / 1.0),
    which loads the IPF and gives the integerizer more competing constraints. Revisit
    band-by-band on measured evidence instead.
  - *Derive the 18 boundary from the published band systems instead of the single-year
    columns.* Not viable: each Zensus table is perturbed independently, so
    `a10bis19 − (Unter18 − Unter10)` is NEGATIVE in 510,719 of 3,148,482 cells (min −12) and
    its national total (1,227,209) is ~20 % below the single-year figure (1,525,848).
  - *A KREIS-level fine-age control layered under the coarse 100 m backbone* (DESTATIS
    12411-0018, published, 17 classes). Would fix the regional totals but not the placement,
    and it adds a second geography for the same attribute when the 100 m input already
    carries the needed resolution.
  - *Leave it and treat the age structure as a limitation.* Rejected: the affected cohort is
    the licence threshold and the student/Semesterticket cohort, i.e. exactly where the open
    mode-choice work (#321/#322) needs the population to be right.
- **Consequences:** the synthetic population is NOT byte-identical with the flag ON — that
  is the point. The realised 15-17 / 18-19 counts, and the licence and PT-subscription
  shares of #307, must be re-measured on the next full synthesis
  (`scripts/measure_license_pt_shares.py` plus the DESTATIS age comparison) and recorded in
  a run manifest before #320 closes; no claim about the size of the improvement is made
  here. Control fit against a committed census margin remains control fit, not behavioural
  validation.
