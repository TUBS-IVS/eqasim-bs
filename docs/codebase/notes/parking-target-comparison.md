# Comparison of model arms with the SrV parking references

Mechanism of lever 3 of the parking cost zones v2 design (issue #436, Task 5): `scripts/parking/compare_parking_targets.py`
compares the arms of a model run (OFF, LEGACY ring, ZONES v2, ZONES v2 without garages) with the committed SrV 2023 tables.
It is an analysis aid, never a pipeline stage and never a model input. The garage options it reports on are in
[parking-garage-options.md](parking-garage-options.md), the zone pricing in [parking-cost-zones.md](parking-cost-zones.md); the
data records are `srv2023_city_center_parking` and `srv2023_commute_parking_by_workplace_class`. A table of this script is a
comparison, never a validation, and a stable run is convergence, not validation.

## What it reads and writes

- Per arm: the output directory of the MATSim run (`<output_path>/matsim_output`): the outcome report
  `ITERS/it.N/N.parking_outcomes.csv` of the Java module (CSV v3, or v2 without the two garage columns; the version is
  detected by the header and the rows are keyed by outcome NAME, zone id and purpose, never by position or enum order) and
  `eqasim_trips.csv.gz` (the chosen trips of the FINAL iteration). Arms that price no zones are given with
  `--arm-without-outcomes` and have empty outcome rows with the reason.
- No raw SrV data: the committed tariff table (zone type and workplace class) and the two committed SrV tables, plus the
  zone polygons, a local restricted file that is not in the repository (data record `parking_zones_2026`). The references are READ from the tables; `tests/test_compare_parking_targets.py` checks that no number of the tables
  appears in the script.
- Output: the metric table (`universe`, `universe_size`, `model`, `reference`, `delta_pp`, `reference_source`, `universe_note`
  per row), the per-arm delta table `<out>_arm_deltas.csv` (every arm against every earlier arm, with `n_model` and
  `n_baseline`, the universe sizes of both sides) and `<out>_provenance.json` (inputs with SHA-256, iterations, code state).

## Rules maintainers must keep

- `count` of the report is the number of PRICING CALLS (car alternatives that mode choice evaluated, chosen or not), not trips or
  persons. Shares of the report are shares of evaluated alternatives; chosen trips are used only for the car mode shares.
- The paid share counts every PAID_* call as paid and is therefore an upper bound of the chosen-trip paid share (PAID_EXPECTED
  and zero-cent PAID_* calls count as paid; paying alternatives are less likely chosen). A chosen-trip paid share and the mean
  cents per call need a Java report extension (proposed as issues, not built).
- The expected garage share of a universe is `sum(garage_probability_sum) / sum(count)` over the selected rows (early outcomes
  count with probability 0). It is NOT the E5 calibration universe (all stays of all modes at destinations, no early rules);
  its row is labelled "calibration target, not validation". The commuter garage share and the free shares per workplace class are
  independent checks. A v3 report whose garage columns are 0 in every row flags the garage rows ("no garage option acted").
- Only the agents that replan in an iteration price their alternatives, so one iteration is a small sample. `--iteration
  FIRST-LAST` pools an inclusive range (counts summed cell by cell); the notes name the iterations. Pooling does not change the
  pre-equilibrium status of early iterations, pooled calls are not independent, stable shares are convergence, not validation.
- A difference between two arms is one stochastic run each, without a noise floor or significance test: descriptive only. No knob
  is turned on a difference in the low single-digit percentage points without a repeat arm.
- Every outcome of `braunschweig.parking.cost.OUTCOMES` is classified once as paid, free or other in the script
  (`PAID_OUTCOMES`, `FREE_OUTCOMES`, `OTHER_OUTCOMES`); a new pricing branch fails the import until it is classified.
- The reader is pinned against the literal reports of the Java `ParkingOutcomeReportListenerTest`
  (`JAVA_REPORTS` in the test); change the Java writer and that fixture together.

## Known limitations

- The universe of the Ia and Ib rows is small at 1 % (the smoke of 2026-09-30 had 47 non-home non-commute centre arrivals); the
  table prints the universe size of every row.
- The free-share rows use the street and resident zones of a class only; stays outside every zone carry no class. The SrV class
  covers the whole Kreis or Oberbezirk (for bs_zentrum wider than Ia and Ib).
- Wolfsburg (03103): the production draw maps the class to the proxy class bs_zentrum (ASSUMPTION A1-b), so the row compares with
  the 03103 table value, not with the draw's target.
