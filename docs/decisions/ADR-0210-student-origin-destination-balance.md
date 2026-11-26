# ADR-0210 · 2026-11-26 · The student origin-destination table uses a commune-level weight curve; the commuter weights are an owner assumption because the calibration could not decide

- **Status:** active (branch `claude/i140-student-balance-3b6cbe`, Phase 2 of the student layer). The level of the students (Decision 5) is NOT decided yet.
- **Numbering:** ADR-0210 is the next free id; checked on 2026-11-26 across all branches, the open pull requests and the open issues (ADR-0209 was already taken elsewhere).
- **Issue:** #140 (student layer), #279. Owner decisions of 2026-11-26.
- **Numbers:** every figure of this build is in the run manifest [`docs/runs/student-balance-2026-11-26.yml`](../runs/student-balance-2026-11-26.yml) and in the headers of the committed tables; this record copies none of them.

## Context

The student layer needs the number of students by the Kreis they live in and by the site they study at (the table T of design B3, counted in heads, never in days). The only source of origins is the CHE ranking (Kreis of the school-leaving certificate), published as two "top 10" lists that show only part of the cells. The weights that turn a CHE origin into a commuter depend on distance and are not observed anywhere; the SrV 2023 destination reference of the surveyed Kreise is the only yardstick.

## Decision

1. **Table rules (B3)**, owned in code by `braunschweig/data/education/student_od_table.py`:
   - A cell is the listed value of either CHE view (per institution, per Kreis); a cell shown in both views with different numbers is an error.
   - A cell shown in neither view is censored: it enters as 0 and carries an upper bound (the smallest listed value of every ten-row list it is missing from). The bounds and the count of cells without a bound are written to the table headers.
   - The art academy has no own CHE list. Its cells of the Kreis view are used (the Braunschweig origin cell; every other origin Kreis is censored); the rest of its LSN enrollment counts as locals, unscaled (scale factor 1, no CHE total).
   - Distance-learning institutions (assumption A7) are dropped and counted; they are not in the balance.
   - In the split of a multi-site institution the site in the origin Kreis itself gets weight 1 (A5, A6).
   - Movers (students who do not live in the origin Kreis, A6) can be moved to Braunschweig by a share at named site Kreise.
2. **Weight rule: a continuous curve evaluated per commune of the home Kreis** (anchors at the band midpoints 17.5, 37.5 and 75 km, 0 from 100 km, the design's maximum commute distance A10), averaged with the communes' population. The population averaging is the owner decision of 2026-11-26 (this record; it has no assumption id). The curve replaces the design's hard bands at the population-weighted Kreis centroid.
3. **Calibration rule: decide or give the range.** Two stages are fitted against the SrV destination reference (weights; weights and movers share). A stage is `decided` only if its optimum lies inside the grid and no parameter ranges widely within the noise floor; otherwise it reports its ranges and writes no configuration value. Grid step, mover step and the maximum flat range are ASSUMPTIONS of the lead, CLI arguments.
4. **Outcome:** neither stage could decide (optimum at the bound, all parameters within the noise floor; reasons in the calibration header). The owner chose option C of the decision aid on 2026-11-26 (a weight triple and a movers share at one site Kreis) and set the values as an ASSUMPTION of the owner. The table headers carry `weights_source=owner`. The values are in the manifest.
5. **Gate and level: limit stated, level NOT decided.** The gate table carries two comparisons with opposite blind spots (numbers and rows in the manifest):
   - *Comparison 1, the design's (A2 gate):* the balance against the SrV count of the Kreis, students away all day added. SrV covers only the surveyed municipalities of a Kreis, so this count can understate the Kreis and the gap looks smaller. Flag `gate_triggered`: not triggered with option C.
   - *Comparison 2, whole Kreis:* the 20-29 part of the balance against the SrV share of the 20-29 year olds times the Zensus population 20-29 of the whole Kreis. It covers the whole Kreis but applies the share of the surveyed municipalities, which may differ from the rest of the Kreis, to all of them. Flag `gate_triggered_area_consistent`: triggered with option C.
   - The Zensus-based estimate (Zensus group times the MiD ratio) stands in the same table as a third reference.
   - The owner has not decided the level. Options (design A2): keep the table, scale the level, or extend the movers rule. Until then the table counts the students as built, and any reader must treat the level as open.
   - Other Kreis rows beyond two SrV standard errors are reported, not gating, so `beyond_two_se=True` on a Kreis row next to `gate_triggered=False` is not a contradiction.
   - Inside the candidate set the design's flag flips (option A triggers it, options B and C do not; see `weight_candidates.csv`), and the gate columns were part of the decision aid. The gate flag was thus one of the aids of the choice, and comparison 1 has the blind spot towards "not triggered".

## Rationale for the options

The decision aid [`weight_candidates.csv`](../runs/artifacts/student-balance-2026-11-26/weights_decision/weight_candidates.csv) scores each candidate against eight SrV yardsticks (columns `z_*`, `sum_squared_z_yardsticks`; the z values rank candidates and are no test statistic) and carries the gate columns.

- **Why not the calibration optima:** both imply a share of education legs of 50 km or more above the SrV share (column `z_distance_50_km_or_more`, both rows positive). The model side of that yardstick is the straight line times the detour factor 1.3 (`circuity.LEGACY_DETOUR_FACTOR`, an ASSUMPTION; sensitivity columns for 1.2 and 1.4), the SrV side is routed. Option C is also above the SrV share, by less. The SrV evidence for movers is the share of students living in Braunschweig who study in Wolfenbüttel: a thin base of few respondents with a gap of little more than one standard error (counts in the manifest), and the calibration did not identify a mover share.
- **Where option C loses:** compared with options A and B, option C is further from SrV on `z_leave_region` and `z_to_region_hannover` (both), on `z_bs_study_in_bs` and `z_bs_leave_region` (than B) and on `z_distance_25_50_km` (than A); on the pooled level (`level_difference_in_srv_se_03ZGB`) it is further than both. It is chosen because it is closer on the 50 km share, the Wolfenbüttel yardstick, `z_to_other_outside` and the summed squares, not because it fits everywhere. The SrV standard errors of the aid are approximate (ASSUMPTION in its header).

## Rejected options

- **Hard bands at the Kreis centroid** (design as first written): not measured how much of a Kreis's population lies in another band than its centroid; no committed table shows it. The only committed evidence is the comparison run: its optimum differs from the commune-curve optimum in both stages, its objective is higher in both stages, and it is also undecided (values in the manifest). Kept as the comparison run `comparison_centroid_bands/` (calibration) and `options/comparison_centroid_bands/` (tables).
- **Option A (`option_a_stage_weights_optimum` in `weight_candidates.csv`):** the weights optimum without movers. Row values: highest `z_distance_50_km_or_more` and highest `sum_squared_z_yardsticks` of the three named options; it triggers the design's gate (`gate_triggered`).
- **Option B (`option_b_stage_weights_and_movers_optimum`):** the weights-and-movers optimum with movers at three site Kreise. Loses to option C on `z_distance_50_km_or_more` and `sum_squared_z_yardsticks`.

## Consequences

- The five committed tables (record `student_od_balance`, `student_level_table`) carry values set by the owner, not calibrated ones. Any reader must treat the weights as an assumption; the SrV reference does not identify them (ranges in the manifest).
- Known limits, with their figures in the manifest: the in-commuters are observed nowhere (only the CHE counts bound them); the pooled level lies below SrV and the cause is not measured (candidate explanations: distance learning and institutions outside the choice set are not in the balance; the balance counts enrolled students, SrV counts students by main activity, two errors in opposite directions, A16; SrV coverage of the Kreis); Goslar lies far above SrV, which covers selected municipalities of that Kreis only; the level is not decided (Decision 5).
- The tables count heads; the day factor is written to the header for Phases 3 and 4 only.
- This is a table build and a calibration on survey aggregates, not a validation.
