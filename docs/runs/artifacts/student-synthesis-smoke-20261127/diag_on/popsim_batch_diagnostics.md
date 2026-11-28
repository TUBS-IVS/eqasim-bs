# PopulationSim batch diagnostics

- work directory: `RUN_DIR/popsim_work`
- batches: 1
- seed groups: 1 (6dd281036d6a: 1 batch(es))
- repository commit: `7c6edc8fd7d121ff497a5dc7e0d27e99624b0e37`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | 6dd281036d6a | 6dd281036d6a | 25649 | 2.181e+05 | 16717 | 0.07665 | 5735 | 80 | 0.08 | 0.2783 | 0.5432 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| 6dd281036d6a | <1 | 158006 | 0.2759 | 0.279 | 0.3097 |
| 6dd281036d6a | 1-2 | 33521 | 0.2139 | 0.2112 | -0.2669 |
| 6dd281036d6a | 2-3 | 11603 | 0.1296 | 0.1208 | -0.8742 |
| 6dd281036d6a | 3-5 | 8792 | 0.1535 | 0.1481 | -0.5378 |
| 6dd281036d6a | 5-8 | 4027 | 0.1144 | 0.1233 | 0.8912 |
| 6dd281036d6a | 8+ | 2148 | 0.1128 | 0.1175 | 0.478 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| 6dd281036d6a | age_sex | persons | True | 22 | 485709 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | employed_grid | persons | True | 10 | 230762 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | education_band | persons | True | 11 | 485709 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | student_work_leg | persons | True | 3 | 46997 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | student_season_ticket | persons | True | 3 | 46997 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1199 | 2.053 |
| 6dd281036d6a | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.03029 | 0.334 |
| 6dd281036d6a | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.4939 | 0.9314 |
| 6dd281036d6a | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | household_minor | households | False | 2 | 218097 | 169869 | 0 | 0.7773 | 0.8582 | 1.104 |
| 6dd281036d6a | tenure | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | building_type | households | True | 3 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | own_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | own_bikes | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | economic_status | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | number_of_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | number_of_bicycles | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| 6dd281036d6a | has_ebike | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |

## Resolved seed records by raw code

| seed_group | attribute | raw_codes | seed_table | n_records | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|
| 6dd281036d6a | HP_SEX_raw | 3,9 | persons | 880 | 0.002032 | 0.003694 | 1.817 |
| 6dd281036d6a | H_MIETE_raw | not 1,2 | households | 5320 | 0.03504 | 0.03139 | 0.8957 |
| 6dd281036d6a | haustyp_raw | 95 | households | 8322 | 0.06185 | 0.05287 | 0.8548 |

## Control universe

| group | geography | target_sum | household_total | ratio |
|---|---|---|---|---|
| household_size | ZENSUS100m | 2.565e+04 | 2.565e+04 | 0.9999 |
| tenure | ZENSUS100m | 2.565e+04 | 2.565e+04 | 1 |
| building_type | ZENSUS100m | 2.565e+04 | 2.565e+04 | 0.9999 |
| own_cars | ZENSUS1km | 2.565e+04 | 2.565e+04 | 1 |
| own_bikes | ZENSUS1km | 2.565e+04 | 2.565e+04 | 0.9999 |
| economic_status | KREIS | 2.565e+04 | 2.565e+04 | 1 |
| number_of_cars | KREIS | 2.565e+04 | 2.565e+04 | 1 |
| number_of_bicycles | KREIS | 2.565e+04 | 2.565e+04 | 0.9999 |
| has_ebike | KREIS | 2.565e+04 | 2.565e+04 | 1 |

## Control fit per geography (region)

| scope | control | geography | importance | target_sum | synthetic_sum | abs_error | bias | wape |
|---|---|---|---|---|---|---|---|---|
| region_geography | ALL | ZENSUS100m |  | 1.699e+05 | 1.699e+05 | 1.003e+04 | -4.709e-05 | 0.05902 |
| region_geography | ALL | ZENSUS1km |  | 8.057e+04 | 8.058e+04 | 243 | 0.0001365 | 0.003016 |
| region_geography | ALL | KREIS |  | 5.188e+05 | 5.187e+05 | 1157 | -0.0002335 | 0.002229 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 4 | 1 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS100m | 23 | 2 | 21 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Household conservation per level (summed over batches)

`abs_deviation_share` = sum over (household group, parent zone) pairs of `|sub integer weight sum - parent integer weight|`, divided by the parent weight sum; 0 means every sub level adds up to its parent.

| level | parent_level | n_pairs | parent_weight_sum | sub_weight_sum | abs_deviation_sum | abs_deviation_share |
|---|---|---|---|---|---|---|
| KREIS | STAAT | 16354 | 2.565e+04 | 2.565e+04 | 0 | 0 |
| ZENSUS1km | KREIS | 16426 | 2.565e+04 | 2.565e+04 | 0 | 0 |
| ZENSUS100m | ZENSUS1km | 22065 | 2.565e+04 | 2.565e+04 | 0 | 0 |

## Input consistency (20 largest shares)

`share` = contradiction `amount` / `base` of the check; Kreis rows compare the KREIS control table with the grid tables, cell rows are contradictions inside the 100 m cells (`braunschweig.popsim.input_consistency`); `ratio_reported` rows (survey ratio, no bound) are in `input_consistency.csv` only.

| batch | scope | key | check | cells | amount | base | share |
|---|---|---|---|---|---|---|---|
| batch_030 | kreis | 03157 | persons_0_17 | 1 | 1.68 | 6 | 0.28 |
| batch_030 | kreis | 03157 | persons_14plus_low | 1 | 2.012 | 24 | 0.08383 |
| batch_030 | cell | all | persons_above_size_maximum | 275 | 2013 | 4.47e+04 | 0.04504 |
| batch_030 | cell | all | persons_below_size_minimum | 331 | 1963 | 4.47e+04 | 0.04392 |
| batch_030 | kreis | 03157 | persons_all | 1 | 0.9919 | 30 | 0.03306 |
| batch_030 | cell | all | size_partition | 309 | 340 | 2.565e+04 | 0.01326 |
| batch_030 | cell | all | building_type_partition | 173 | 176 | 2.565e+04 | 0.006862 |
| batch_030 | kreis | 03154 | persons_all | 1 | 9.53 | 1578 | 0.006039 |
| batch_030 | kreis | 03154 | persons_0_17 | 1 | 1.437 | 250 | 0.005749 |
| batch_030 | cell | all | tenure_partition | 131 | 131 | 2.565e+04 | 0.005107 |
| batch_030 | cell | all | households_without_persons | 11 | 35 | 2.565e+04 | 0.001365 |
| batch_030 | kreis | 03158 | persons_0_17 | 1 | 1.085 | 877 | 0.001238 |
| batch_030 | kreis | 03154 | households | 1 | 0.7193 | 717 | 0.001003 |
| batch_030 | kreis | 03158 | persons_all | 1 | 4.32 | 5122 | 0.0008435 |
| batch_030 | cell | all | persons_without_households | 3 | 28 | 4.47e+04 | 0.0006265 |
| batch_030 | kreis | 03157 | households | 1 | 0.003037 | 5 | 0.0006075 |
| batch_030 | kreis | 03101 | persons_0_17 | 1 | 2.775 | 4729 | 0.0005868 |
| batch_030 | kreis | 03158 | households | 1 | 1.014 | 2366 | 0.0004285 |
| batch_030 | kreis | 03101 | households | 1 | 1.298 | 2.256e+04 | 5.752e-05 |
| batch_030 | kreis | 03101 | persons_all | 1 | 0.5593 | 3.797e+04 | 1.473e-05 |

Skipped for absent columns (not measured):

| scope | key | batches |
|---|---|---|
| kreis | persons_18plus | 1 |

## Household composition

Synthetic households with the members of their seed household; adult = age >= 18. Definitions: `braunschweig.analysis.popsim_batch_diagnostics.composition`.

| batch | households | households_with_minor | share_households_with_minor | one_adult_share_among_minor_households | persons_0_17_per_minor_household | share_persons_0_9_with_one_adult | share_persons_10_14_with_one_adult | minor_households_without_adult |
|---|---|---|---|---|---|---|---|---|
| batch_030 | 25649 | 3638 | 0.1418 | 0.2254 | 1.613 | 0.1846 | 0.2933 | 1 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| coverage_ratio | schulabschluss | 2.053 | 2 |
| coverage_ratio | beruflabschluss | 0.334 | 0.5 |
