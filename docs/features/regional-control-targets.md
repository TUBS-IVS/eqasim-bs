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
