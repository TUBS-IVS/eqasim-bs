# Golden KREIS batch tables (#658, Task A0g)

The `control_totals_*.csv` tables of two PopulationSim batch folders (`batch_000`, `batch_001`),
written by `braunschweig.popsim.mid.assemble_batch_folder` from the SYNTHETIC input of
`tests/popsim_kreis_batch_fixture.py`. The values are test data, not Zensus or MiD figures.

**Frozen at base commit `0a611669`, before any #658 change.** Part A changes how the KREIS
control table of a batch is built; with every new key at its legacy value the tables must stay
value for value the same (`tests/test_popsim_kreis_batch_golden.py`). The four tables of the
control build without a KREIS geography are frozen one directory up (#657).

## Layout of the input

- Two Kreise (03101, 03158). 03101 is split over both batches, 03158 lies in `batch_000` only.
- `batch_001` is child-rich (children aged 0-19 at 2.5 times the base cell values), so the two
  batches differ in age structure.

## Control families present

- KREIS, person level: `employed`, the schulabschluss and beruflabschluss classes (tier 3), and
  every entry of `kreis_attribute_control.REGISTRY`: `trip_class_*`, `employment_status_*`,
  `pt_ticket_group*`, `*_participation_*`, `work_by_employment_*`, `education_0_5_*`,
  `education_6_17_*`, `education_18plus_*`.
- KREIS, household level: `economic_status_*`, `number_of_cars_*`, `number_of_bicycles_*`,
  `has_ebike_*` (apportioned by the household share).
- 100 m grid: `OWN_CARS_<c>_agg`, `OWN_BIKES_<c>_agg` (as 1 km bases of the catalog), the ten
  `EMPLOYED_{M,F}_*_agg`, the 22 age x sex bands (fine teen bands on), the building types.
- STAAT, WELT, ZENSUS1km, ZENSUS100m tables of each batch.

## Not covered

- The single-year `M_AGE_<y>` / `F_AGE_<y>` columns are present in the synthetic cells (they
  feed the age-range KREIS totals) but are not control columns of any table, so no golden
  column shows them directly.
- The per-Kreis targets are synthetic shares fed through the production count function; the
  committed `target2026_*` CSVs and the Zensus Kreis table are not read, so the loaders are
  not covered here.

## Regenerate

Only on a deliberate, documented change of the expected tables, from the repo root:

    python -m tests.popsim_kreis_batch_fixture
