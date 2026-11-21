# ADR-0205 · 2026-11-20 · Wolfsburg's e-bike target moves the SrV region share by the MiD H12.1 ratio; its other SrV-only rows stay unchanged after a backtest

- **Status:** active (branch `feature/wob-has-ebike-mid-direct`, PR #675).
- **Numbering:** ADR-0205 is the next free id; checked on 2026-11-20 across all remote branches, the open pull requests and the open issues (the highest id taken is ADR-0204).
- **Issue:** none; owner decisions of 2026-11-20 and 2026-11-21 (the second after the first review round of PR #675) to implement the change directly.

## Context

- SrV 2023 surveys seven of the eight ZGB Kreise; Wolfsburg (03103) is not surveyed. The blended Kreis targets give Wolfsburg its MiD value (rule of `braunschweig.popsim.blended_targets`). The SrV-only targets gave it the SrV region total: `has_ebike`, the participation targets and `education_by_age`; `trip_class` scales the region total's `trips_0` by Wolfsburg's relative MiD P36.1 immobility (`scripts/build_trip_class_target.py`).
- The owner asked whether Wolfsburg's rows should instead be derived from the MiD 2023 regional report (infas 7555), for example as the SrV value of Braunschweig or of the region moved by the MiD ratio Wolfsburg / anchor.
- The report prints one row per Kreis (`Teilgebiete`) in integer row-%. Wolfsburg's rows rest on few cases (column `n_unweighted` of the committed tables), so small shares are coarse. The `Gesamt` row of the report and the SrV region total weight the Kreise differently (their own `n_weighted` columns), and the SrV region never holds Wolfsburg, so a region anchor must be built the same way in both surveys.

## Decision

1. **`has_ebike`: Wolfsburg = SrV region share x MiD H12.1 odds ratio.** Its row is the SrV region share of households with at least one e-bike or pedelec, moved by the MiD H12.1 odds ratio of Wolfsburg to the MiD region without Wolfsburg (`keine_angabe` renormalized out; Wolfsburg leaves the MiD `Gesamt` row by its weighted households). `source` = `srv_region_total_mid_h12_1_odds`, `n_effective` = Wolfsburg's MiD households. The seven SrV Kreise and the `Gesamt` row stay SrV. `scripts/build_blended_kreis_targets.py` (`build_has_ebike`; the formulas `odds_transfer` and `leave_one_out` in `braunschweig.popsim.blended_targets`) raises when the MiD Wolfsburg or `Gesamt` row is missing; there is no fallback to the plain region share.
2. **`trip_class` keeps its rule, the region total with the MiD P36.1 immobility correction.** With decision 1 both Wolfsburg rows that have a suitable MiD per-Kreis table follow one rule, "SrV region value moved by the MiD ratio of Wolfsburg". ASSUMPTION: this consistency is worth more than the small backtest advantage of the plain region total (Rationale). One difference stays: the `trip_class` rule divides by the MiD `Gesamt` row with Wolfsburg in it, decision 1 by the MiD region without Wolfsburg.
3. **The other SrV-only Wolfsburg rows are unchanged.** Leisure and escort participation keep the plain SrV region total. The work and education rows were not tested: the report has no per-Kreis table of the same construct with enough Wolfsburg cases.
4. **Two report tables are committed:** H12.1 (input of decision 1) and P39.1 (input of the backtest), both written by `scripts/extract_mid_tables.py`, which now raises when a page lacks the `Gesamt` row or one of the eight Kreise.

## Rationale

The backtest `scripts/backtest_wolfsburg_kreis_transfer.py` treats each SrV Kreis as if it were not surveyed, predicts its SrV value from an anchor (Braunschweig, or the region without that Kreis; on the MiD side Wolfsburg leaves the region too, because the SrV region never holds it) and scores the prediction against the SrV value. The figures are in the evidence bundle (Evidence); this section states what they show.

- **`has_ebike`:** over the seven Kreise the odds transfer from the region anchor has the smallest error of all rules and beats the MiD value of the Kreis itself in every Kreis except Braunschweig, Salzgitter included. A Braunschweig anchor is the worst rule. The region transfer misses Braunschweig, where the two surveys agree while SrV lies far above MiD in the Landkreise.
- **`trip_class`:** over all Kreise neither transfer (P39.1 classes, P36.1 immobility) beats copying the region, the current P36.1 rule included, also on `trips_0`, the only class it changes. The gap to the plain region total is small next to the gaps for `has_ebike`, leisure and escort (`backtest_summary.csv`), which is why decision 2 lets consistency decide.
- **Leisure and escort:** inside SrV the trips per participant are nearly the same in every Kreis (`trips_per_participant.csv`), so participation is proportional to the purpose trip rate, and a transfer with SrV's own rate beats copying the anchor (`*_srv_rate` rows). With the MiD rate, the only rate available for Wolfsburg, the transfer misses the SrV Kreise by more than copying the region does (`*_mid_rate` rows). The assumption holds; the MiD per-Kreis purpose shares are too coarse to carry it.

## Rejected options

- **`has_ebike` from the plain SrV region share** (the previous rule) and **from the MiD value of Wolfsburg itself** (this PR's first version): larger backtest errors than the region transfer; the MiD value wins only in Braunschweig.
- **Ratio transfers from a Braunschweig anchor** (`has_ebike`, leisure, escort, `trip_class` P39.1): worse than the region anchor.
- **MiD-rate transfers for leisure and escort, and P39.1 for `trip_class`:** worse than copying the region.
- **`trip_class` on the plain region total:** slightly smaller backtest error, but it breaks the common rule of decision 2 (owner decision of 2026-11-21).

## Consequences

- Wolfsburg's soft `has_ebike` target is lower than the previous plain region share (row 03103 of `target2026_has_ebike_by_kreis.csv`), so the synthetic Wolfsburg households own fewer e-bikes. The seven other Kreise and every other target table keep their values (regenerated content-identical; some headers changed wording only). The effect on a synthetic population is not measured here; no run manifest exists yet.
- The PopulationSim stage's synpp token covered source code only, so a changed target CSV would have left a warm cache serving the old output (`docs/codebase/notes/synpp-helper-hash-audit.md`, "Data files are invisible"). The same branch adds the content of every committed data file the stage reads, and of the PopulationSim settings file, to its token (what is covered and the deliberate gap of local-only inputs: the docstring of `braunschweig.popsim.stage.validate`). Every existing PopulationSim cache therefore recomputes once, which this change needs anyway.
- Regenerating the backtest needs the local-only SrV 2023 microdata; the targets need committed tables only.

## Evidence

- Backtest bundle [`eqasim-data/data/braunschweig/calibration/wolfsburg_transfer_backtest_2026-11-20/`](../../eqasim-data/data/braunschweig/calibration/wolfsburg_transfer_backtest_2026-11-20/): `backtest_summary.csv` (mean absolute errors per attribute and anchor, all Kreise and the two kreisfreie Staedte), `backtest_errors.csv` (every prediction), `trips_per_participant.csv`. Written by `scripts/backtest_wolfsburg_kreis_transfer.py`; its formulas: `tests/test_backtest_wolfsburg_kreis_transfer.py`.
- Target rule: `tests/test_blended_target_tables.py` (the Wolfsburg row as the region odds transfer, `keine_angabe` renormalized out, and the failure without a needed MiD row). Report parsing: `tests/test_extract_mid_tables.py`.
