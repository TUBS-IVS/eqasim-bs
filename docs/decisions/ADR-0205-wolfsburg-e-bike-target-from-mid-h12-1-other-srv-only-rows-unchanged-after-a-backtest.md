# ADR-0205 · 2026-11-20 · Wolfsburg's e-bike target comes from MiD H12.1; its other SrV-only rows stay unchanged after a backtest

- **Status:** active (branch `feature/wob-has-ebike-mid-direct`).
- **Numbering:** ADR-0205 is the next free id; checked on 2026-11-20 across all remote branches, the open pull requests and the open issues (the highest id taken is ADR-0204).
- **Issue:** none; owner decision of 2026-11-20 to implement the change directly.

## Context

- SrV 2023 surveys seven of the eight ZGB Kreise; Wolfsburg (03103) is not surveyed. The blended Kreis targets give Wolfsburg its MiD value (rule of `braunschweig.popsim.blended_targets`). The SrV-only targets gave it the SrV region total: `has_ebike`, the participation targets and `education_by_age`; `trip_class` scales the region total's `trips_0` by Wolfsburg's relative MiD P36.1 immobility (`scripts/build_trip_class_target.py`).
- The owner asked whether Wolfsburg's rows should instead be derived from the MiD 2023 regional report (infas 7555), for example as the SrV value of Braunschweig or of the region moved by the MiD ratio Wolfsburg / anchor.
- The report prints one row per Kreis (`Teilgebiete`) in integer row-%. Wolfsburg's rows rest on few cases (column `n_unweighted` of the committed tables), so small shares are coarse. The `Gesamt` row of the report and the SrV region total weight the Kreise differently (their own `n_weighted` columns), so a region anchor must be built the same way in both surveys.

## Decision

1. **`has_ebike`: Wolfsburg = MiD H12.1.** Its row is the share of households with at least one e-bike or pedelec in MiD table H12.1, with the `keine_angabe` mass renormalized out, `source` = `mid`: the "Wolfsburg = MiD" rule of the blended household attributes. The seven SrV Kreise and the `Gesamt` row stay SrV. `scripts/build_blended_kreis_targets.py` (`build_has_ebike`, `mid_h12_1_ebike_share`) raises when the MiD row is missing; there is no fallback to the region total. ASSUMPTION: Wolfsburg resembles Braunschweig more than the region; the backtest supports this choice only through Braunschweig (Rationale).
2. **Every other SrV-only Wolfsburg row is unchanged.** Leisure and escort participation keep the SrV region total. `trip_class` keeps its P36.1 rule although the backtest scores it worse than a plain region copy; switching it is left to a separate owner decision, so this ADR does not change a hard control on that evidence (ASSUMPTION until then). The work and education rows were not tested: the report has no per-Kreis table of the same construct with enough Wolfsburg cases.
3. **Two report tables are committed:** H12.1 (input of decision 1) and P39.1 (input of the backtest), both written by `scripts/extract_mid_tables.py`, which now raises when a page lacks the `Gesamt` row or one of the eight Kreise.

## Rationale

The backtest `scripts/backtest_wolfsburg_kreis_transfer.py` treats each SrV Kreis as if it were not surveyed, predicts its SrV value from an anchor (Braunschweig, or the region without that Kreis; on the MiD side Wolfsburg leaves the region too, because the SrV region never holds it) and scores the prediction against the SrV value. The figures are in the evidence bundle (Evidence); this section states what they show.

- **Leisure and escort:** inside SrV the trips per participant are nearly the same in every Kreis (`trips_per_participant.csv`), so participation is proportional to the purpose trip rate, and a transfer with SrV's own rate beats copying the anchor (`*_srv_rate` rows). With the MiD rate, the only rate available for Wolfsburg, the transfer misses the SrV Kreise by more than copying the region does (`*_mid_rate` rows). The assumption holds; the MiD per-Kreis purpose shares are too coarse to carry it.
- **`trip_class`:** over all Kreise neither transfer (P39.1 classes, P36.1 immobility) beats copying the region, the current P36.1 rule included, also on `trips_0`, the only class it changes. With a Braunschweig anchor P39.1 is closer for Salzgitter; with the region anchor it is not. The evidence therefore favours the plain region total over the current rule (Decision 2).
- **`has_ebike`:** a Braunschweig anchor is the worst rule. Over the seven Kreise the odds transfer from the region anchor has the smallest error and beats the MiD value of the Kreis itself everywhere except in Braunschweig, Salzgitter included. The MiD value wins only in Braunschweig, where the region copy and the region transfer miss by the largest errors of any rule (in the Landkreise SrV lies far above MiD; in Braunschweig the two surveys agree). The decided rule rests on Wolfsburg's MiD share being close to Braunschweig's, and on the "Wolfsburg = MiD" rule of the other household attributes; the region transfer would give Wolfsburg a share a few points higher. Both lie below the previous region copy.

## Rejected options

- **`has_ebike` from the SrV region total** (the previous rule): it carries the Landkreis level into a city.
- **Ratio transfers from a Braunschweig anchor** (`has_ebike`, leisure, escort, `trip_class` P39.1): worse than copying the region or than the region transfer.
- **MiD-rate transfers for leisure and escort, and P39.1 for `trip_class`:** worse than copying the region.
- **`has_ebike` from the region odds transfer:** lowest error over the seven Kreise, but it carries part of the Landkreis level into a city; not chosen (ASSUMPTION in Decision 1). The owner may revisit this choice.

## Consequences

- Wolfsburg's soft `has_ebike` target is lower (row 03103 of `target2026_has_ebike_by_kreis.csv`), so the synthetic Wolfsburg households own fewer e-bikes. The seven other Kreise and every other target table are unchanged (regenerated content-identical). The effect on a synthetic population is not measured here; no run manifest exists yet.
- The PopulationSim stage's synpp token covered source code only, so a changed target CSV would have left a warm cache serving the old output (`docs/codebase/notes/synpp-helper-hash-audit.md`, "Data files are invisible"). The same branch adds the content of every committed data file the stage reads, and of the PopulationSim settings file, to its token (what is covered and the deliberate gap of local-only inputs: the docstring of `braunschweig.popsim.stage.validate`). Every existing PopulationSim cache therefore recomputes once, which this change needs anyway.
- Regenerating the backtest needs the local-only SrV 2023 microdata; the targets need committed tables only.

## Evidence

- Backtest bundle [`eqasim-data/data/braunschweig/calibration/wolfsburg_transfer_backtest_2026-11-20/`](../../eqasim-data/data/braunschweig/calibration/wolfsburg_transfer_backtest_2026-11-20/): `backtest_summary.csv` (mean absolute errors per attribute and anchor, all Kreise and the two kreisfreie Staedte), `backtest_errors.csv` (every prediction), `trips_per_participant.csv`. Written by `scripts/backtest_wolfsburg_kreis_transfer.py`; its formulas: `tests/test_backtest_wolfsburg_kreis_transfer.py`.
- Target rule: `tests/test_blended_target_tables.py` (the Wolfsburg row from MiD H12.1, `keine_angabe` renormalized out, and the failure without the MiD row). Report parsing: `tests/test_extract_mid_tables.py`.
