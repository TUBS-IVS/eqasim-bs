# ADR-0205 · 2026-11-20 · Wolfsburg's e-bike target comes from MiD H12.1; its other SrV-only rows stay unchanged after a backtest

- **Status:** active (branch `feature/wob-has-ebike-mid-direct`).
- **Numbering:** ADR-0205 is the next free id; checked on 2026-11-20 across all remote branches, the open pull requests and the open issues (the highest id taken is ADR-0204).
- **Issue:** none; owner decision of 2026-11-20 to implement the change directly.

## Context

- SrV 2023 surveys seven of the eight ZGB Kreise; Wolfsburg (03103) is not surveyed. The blended Kreis targets give Wolfsburg its MiD value (rule of `braunschweig.popsim.blended_targets`). The SrV-only targets gave it the SrV region total: `has_ebike`, the participation targets and `education_by_age`; `trip_class` scales the region total's `trips_0` by Wolfsburg's relative MiD P36.1 immobility (`scripts/build_trip_class_target.py`).
- The owner asked whether Wolfsburg's rows should instead be derived from the MiD 2023 regional report (infas 7555), for example as the SrV value of Braunschweig or of the region moved by the MiD ratio Wolfsburg / anchor.
- The report prints one row per Kreis (`Teilgebiete`) in integer row-%. Wolfsburg's rows rest on few cases (column `n_unweighted` of the committed tables), so small shares are coarse. The `Gesamt` row of the report and the SrV region total weight the Kreise differently (their own `n_weighted` columns), so a region anchor must be built the same way in both surveys.

## Decision

1. **`has_ebike`: Wolfsburg = MiD H12.1.** Its row is the share of households with at least one e-bike or pedelec in MiD table H12.1, with the `keine_angabe` mass renormalized out, `source` = `mid`: the "Wolfsburg = MiD" rule of the blended household attributes. The seven SrV Kreise and the `Gesamt` row stay SrV. `scripts/build_blended_kreis_targets.py` (`build_has_ebike`, `mid_h12_1_ebike_share`) raises when the MiD row is missing; there is no fallback to the region total.
2. **Every other SrV-only Wolfsburg row is unchanged.** Leisure and escort participation keep the SrV region total; `trip_class` keeps its P36.1 rule. The work and education rows were not tested: the report has no per-Kreis table of the same construct with enough Wolfsburg cases.
3. **Two report tables are committed:** H12.1 (input of decision 1) and P39.1 (input of the backtest), both written by `scripts/extract_mid_tables.py`, which now raises when a page lacks the `Gesamt` row or one of the eight Kreise.

## Rationale

The backtest `scripts/backtest_wolfsburg_kreis_transfer.py` treats each SrV Kreis as if it were not surveyed, predicts its SrV value from an anchor (Braunschweig, or the region without that Kreis, leave-one-out in both surveys) and scores the prediction against the SrV value. The figures are in the evidence bundle (Evidence); this section states what they show.

- **Leisure and escort:** inside SrV the trips per participant are nearly the same in every Kreis (`trips_per_participant.csv`), so participation is proportional to the purpose trip rate, and a transfer with SrV's own rate beats copying the anchor (`*_srv_rate` rows). With the MiD rate, the only rate available for Wolfsburg, the transfer misses the SrV Kreise by more than copying the region does (`*_mid_rate` rows). The assumption holds; the MiD per-Kreis purpose shares are too coarse to carry it.
- **`trip_class`:** over all Kreise neither transfer (P39.1 classes, P36.1 immobility) beats copying the region; P39.1 is closer only for the two kreisfreie Staedte. No change is supported.
- **`has_ebike`:** a Braunschweig anchor is the worst rule. For the two kreisfreie Staedte the MiD value of the Kreis itself has the smallest mean error (`bs_` / `sz_` columns), while the region copy misses Braunschweig by the largest error of any rule: in the Landkreise SrV lies far above MiD, in the cities the two surveys agree. Wolfsburg is a kreisfreie Stadt whose MiD share is close to Braunschweig's.

## Rejected options

- **`has_ebike` from the SrV region total** (the previous rule): it carries the Landkreis level into a city.
- **Ratio transfers from a Braunschweig or a region anchor** (`has_ebike`, leisure, escort, `trip_class` P39.1): worse than the decided rule in the backtest, or not better than the current one.

## Consequences

- Wolfsburg's soft `has_ebike` target is lower (row 03103 of `target2026_has_ebike_by_kreis.csv`), so the synthetic Wolfsburg households own fewer e-bikes. The seven other Kreise and every other target table are unchanged (regenerated content-identical). The effect on a synthetic population is not measured here; no run manifest exists yet.
- The PopulationSim stage's synpp token covered source code only, so a changed target CSV would have left a warm cache serving the old output (`docs/codebase/notes/synpp-helper-hash-audit.md`, "Data files are invisible"). The same branch adds the content of every committed data file the stage reads, and of the PopulationSim settings file, to its token (what is covered and the deliberate gap of local-only inputs: the docstring of `braunschweig.popsim.stage.validate`). Every existing PopulationSim cache therefore recomputes once, which this change needs anyway.
- Regenerating the backtest needs the local-only SrV 2023 microdata; the targets need committed tables only.

## Evidence

- Backtest bundle [`eqasim-data/data/braunschweig/calibration/wolfsburg_transfer_backtest_2026-11-20/`](../../eqasim-data/data/braunschweig/calibration/wolfsburg_transfer_backtest_2026-11-20/): `backtest_summary.csv` (mean absolute errors per attribute and anchor, all Kreise and the two kreisfreie Staedte), `backtest_errors.csv` (every prediction), `trips_per_participant.csv`. Written by `scripts/backtest_wolfsburg_kreis_transfer.py`; its formulas: `tests/test_backtest_wolfsburg_kreis_transfer.py`.
- Target rule: `tests/test_blended_target_tables.py` (the Wolfsburg row from MiD H12.1, `keine_angabe` renormalized out, and the failure without the MiD row). Report parsing: `tests/test_extract_mid_tables.py`.
