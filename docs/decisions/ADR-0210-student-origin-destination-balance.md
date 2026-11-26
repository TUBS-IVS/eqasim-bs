# ADR-0210 · 2026-11-26 · The student origin-destination table uses a commune-level weight curve; the commuter weights are an owner assumption because the calibration could not decide

- **Status:** active (branch `claude/i140-student-balance-3b6cbe`, Phase 2 of the student layer).
- **Numbering:** ADR-0210 is the next free id; checked on 2026-11-26 across all branches, the open pull requests and the open issues (ADR-0209 was already taken elsewhere).
- **Issue:** #140 (student layer), #279. Owner decisions of 2026-11-26.
- **Numbers:** every figure of this build is in the run manifest [`docs/runs/student-balance-2026-11-26.yml`](../runs/student-balance-2026-11-26.yml) and in the headers of the committed tables; this record copies none of them.

## Context

The student layer needs the number of students by the Kreis they live in and by the site they study at (the table T of design B3, counted in heads, never in days). The only source of origins is the CHE ranking (Kreis of the school-leaving certificate), published as two "top 10" lists that show only part of the cells. The weights that turn a CHE origin into a commuter depend on distance and are not observed anywhere; the SrV 2023 destination reference of the surveyed Kreise is the only yardstick.

## Decision

1. **Table rules (B3)**, owned in code by `braunschweig/data/education/student_od_table.py`:
   - A cell is the listed value of either CHE view (per institution, per Kreis); a cell shown in both views with different numbers is an error.
   - A cell shown in neither view is censored: it enters as 0 and carries an upper bound (the smallest listed value of every ten-row list it is missing from). The bounds and the count of cells without a bound are written to the table headers.
   - The art academy has no own CHE list; it enters through its LSN enrollment as local residents, unscaled.
   - Distance-learning institutions (assumption A7) are dropped and counted; they are not in the balance.
   - In the split of a multi-site institution the site in the origin Kreis itself gets weight 1 (A5, A6).
   - Movers (students who do not live in the origin Kreis, A6) can be moved to Braunschweig by a share at named site Kreise.
2. **Weight rule: a continuous curve evaluated per commune of the home Kreis** (anchors at the band midpoints 17.5, 37.5 and 75 km, 0 from 100 km), averaged with the communes' population (ASSUMPTION A10). It replaces the design's hard bands at the population-weighted Kreis centroid. Owner decision of 2026-11-26.
3. **Calibration rule: decide or give the range.** Two stages are fitted against the SrV destination reference (weights; weights and movers share). A stage is `decided` only if its optimum lies inside the grid and no parameter ranges widely within the noise floor; otherwise it reports its ranges and writes no configuration value. Grid step, mover step and the maximum flat range are ASSUMPTIONS of the lead, CLI arguments.
4. **Outcome:** neither stage could decide (optimum at the bound, all parameters within the noise floor; reasons in the calibration header). The owner chose option C of the decision aid on 2026-11-26 (a weight triple and a movers share at one site Kreis) and set the values as an ASSUMPTION of the owner. The table headers carry `weights_source=owner`. The values are in the manifest.
5. **Gate:** not triggered with option C. The gate looks only at the Braunschweig row and the pooled row (design A2); other Kreis rows beyond two SrV standard errors are reported, not gating, so `beyond_two_se=True` on a Kreis row next to `gate_triggered=False` is not a contradiction.

## Rationale for the options

The decision aid [`weight_candidates.csv`](../runs/artifacts/student-balance-2026-11-26/weights_decision/weight_candidates.csv) scores each candidate against eight SrV yardsticks (columns `z_*`, `sum_squared_z_yardsticks`; the z values rank candidates and are no test statistic).

- **Why not the calibration optima:** both imply a share of education legs of 50 km or more (routed) above the SrV share (column `z_distance_50_km_or_more`, both rows positive), and the SrV movers effect exists for Wolfenbüttel only.
- **Where option C loses:** on the yardsticks `z_leave_region` and `z_to_region_hannover` its rows are further from SrV than the two calibration optima. It is chosen because it is closer on the 50 km share, the Wolfenbüttel yardstick and the summed squares, not because it fits everywhere. The SrV standard errors of the aid are approximate (ASSUMPTION in its header).

## Rejected options

- **Hard bands at the Kreis centroid** (design as first written): for several large Kreis-site pairs a large part of the population lies in another band than the centroid. Kept as the comparison run `comparison_centroid_bands/` (calibration) and `options/comparison_centroid_bands/` (tables); its optimum is in the manifest and is also undecided.
- **Option A (`option_a_stage_weights_optimum` in `weight_candidates.csv`):** the weights optimum without movers. Row values: highest `z_distance_50_km_or_more` and highest `sum_squared_z_yardsticks` of the three named options.
- **Option B (`option_b_stage_weights_and_movers_optimum`):** the weights-and-movers optimum with movers at three site Kreise. Loses to option C on `z_distance_50_km_or_more` and `sum_squared_z_yardsticks`.

## Consequences

- The five committed tables (record `student_od_balance`, `student_level_table`) carry values set by the owner, not calibrated ones. Any reader must treat the weights as an assumption; the SrV reference does not identify them (ranges in the manifest).
- Known limits, with their figures in the manifest: the in-commuters are observed nowhere (only the CHE counts bound them); the pooled level lies below SrV because distance learning and institutions outside the choice set are not in the balance (A16); Goslar lies far above SrV, which covers selected municipalities of that Kreis only.
- The tables count heads; the day factor is written to the header for Phases 3 and 4 only.
- This is a table build and a calibration on survey aggregates, not a validation.
