# PopulationSim batch diagnostics

- work directory: `~/i659_b/arm_7a`
- batches: 4
- seed groups: 1 (0031c7e0b140: 4 batch(es))
- repository commit: `6633c522`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | 0031c7e0b140 | 0031c7e0b140 | 54140 | 2.181e+05 | 30954 | 0.1419 | 1.072e+04 | 88 | 0.05471 | 0.2047 | 0.4425 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| 0031c7e0b140 | <1 | 158006 | 0.2759 | 0.2901 | 1.414 |
| 0031c7e0b140 | 1-2 | 33521 | 0.2139 | 0.2151 | 0.1206 |
| 0031c7e0b140 | 2-3 | 11603 | 0.1296 | 0.1203 | -0.9266 |
| 0031c7e0b140 | 3-5 | 8792 | 0.1535 | 0.1507 | -0.281 |
| 0031c7e0b140 | 5-8 | 4027 | 0.1144 | 0.1145 | 0.01498 |
| 0031c7e0b140 | 8+ | 2148 | 0.1128 | 0.1093 | -0.3422 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| 0031c7e0b140 | age_sex | persons | True | 22 | 485709 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | employed_grid | persons | True | 10 | 230762 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1261 | 2.16 |
| 0031c7e0b140 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.03222 | 0.3552 |
| 0031c7e0b140 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5018 | 0.9463 |
| 0031c7e0b140 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | tenure | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | building_type | households | True | 3 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | own_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | own_bikes | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | economic_status | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | number_of_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | number_of_bicycles | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | has_ebike | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |

## Resolved seed records by raw code

| seed_group | attribute | raw_codes | seed_table | n_records | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|
| 0031c7e0b140 | HP_SEX_raw | 3,9 | persons | 880 | 0.002032 | 0.004017 | 1.976 |
| 0031c7e0b140 | H_MIETE_raw | not 1,2 | households | 5320 | 0.03504 | 0.02861 | 0.8165 |
| 0031c7e0b140 | haustyp_raw | 95 | households | 8322 | 0.06185 | 0.05658 | 0.9147 |

## Control universe

| group | geography | target_sum | household_total | ratio |
|---|---|---|---|---|
| household_size | ZENSUS100m | 5.408e+04 | 5.41e+04 | 0.9994 |
| tenure | ZENSUS100m | 5.41e+04 | 5.41e+04 | 0.9999 |
| building_type | ZENSUS100m | 5.411e+04 | 5.41e+04 | 1 |
| own_cars | ZENSUS1km | 5.41e+04 | 5.41e+04 | 0.9999 |
| own_bikes | ZENSUS1km | 5.412e+04 | 5.41e+04 | 1 |
| economic_status | KREIS | 5.411e+04 | 5.41e+04 | 1 |
| number_of_cars | KREIS | 5.41e+04 | 5.41e+04 | 0.9999 |
| number_of_bicycles | KREIS | 5.412e+04 | 5.41e+04 | 1 |
| has_ebike | KREIS | 5.411e+04 | 5.41e+04 | 1 |

## Control fit per geography (region)

| scope | control | geography | importance | target_sum | synthetic_sum | abs_error | bias | wape |
|---|---|---|---|---|---|---|---|---|
| region_geography | ALL | ZENSUS100m |  | 3.67e+05 | 3.672e+05 | 3.717e+04 | 0.0005912 | 0.1013 |
| region_geography | ALL | ZENSUS1km |  | 1.623e+05 | 1.624e+05 | 1510 | 0.0005791 | 0.009302 |
| region_geography | ALL | KREIS |  | 1.1e+06 | 1.1e+06 | 1997 | 0.0001043 | 0.001816 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 4 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 11 | 1 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS100m | 144 | 23 | 121 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Household conservation per level (summed over batches)

`abs_deviation_share` = sum over (household group, parent zone) pairs of `|sub integer weight sum - parent integer weight|`, divided by the parent weight sum; 0 means every sub level adds up to its parent.

| level | parent_level | n_pairs | parent_weight_sum | sub_weight_sum | abs_deviation_sum | abs_deviation_share |
|---|---|---|---|---|---|---|
| KREIS | STAAT | 41856 | 5.41e+04 | 5.41e+04 | 0 | 0 |
| ZENSUS1km | KREIS | 42662 | 5.41e+04 | 5.41e+04 | 0 | 0 |
| ZENSUS100m | ZENSUS1km | 50500 | 5.41e+04 | 5.414e+04 | 143 | 0.002643 |

## Input consistency (20 largest shares)

`share` = contradiction `amount` / `base` of the check; Kreis rows compare the KREIS control table with the grid tables, cell rows are contradictions inside the 100 m cells (`braunschweig.popsim.input_consistency`); `ratio_reported` rows (survey ratio, no bound) are in `input_consistency.csv` only.

| batch | scope | key | check | cells | amount | base | share |
|---|---|---|---|---|---|---|---|
| batch_032 | kreis | 03154 | persons_0_17 | 1 | 0.8124 | 4 | 0.2031 |
| batch_000 | cell | all | persons_below_size_minimum | 255 | 1306 | 1.099e+04 | 0.1188 |
| batch_013 | cell | all | persons_below_size_minimum | 300 | 1671 | 1.646e+04 | 0.1015 |
| batch_000 | cell | all | persons_above_size_maximum | 212 | 1062 | 1.099e+04 | 0.09664 |
| batch_013 | cell | all | persons_above_size_maximum | 256 | 1337 | 1.646e+04 | 0.0812 |
| batch_032 | cell | all | persons_below_size_minimum | 321 | 1771 | 2.612e+04 | 0.06781 |
| batch_028 | kreis | 03158 | persons_0_17 | 1 | 2.501 | 37 | 0.06759 |
| batch_032 | cell | all | persons_above_size_maximum | 289 | 1672 | 2.612e+04 | 0.06402 |
| batch_013 | cell | all | size_partition | 365 | 406 | 7106 | 0.05713 |
| batch_032 | kreis | 03154 | persons_all | 1 | 1.034 | 20 | 0.05168 |
| batch_028 | cell | all | persons_above_size_maximum | 299 | 2387 | 4.702e+04 | 0.05076 |
| batch_000 | cell | all | persons_without_households | 71 | 511 | 1.099e+04 | 0.0465 |
| batch_032 | kreis | 03154 | persons_14plus_high | 1 | 0.7346 | 16 | 0.04591 |
| batch_000 | cell | all | size_partition | 271 | 300 | 6629 | 0.04526 |
| batch_028 | cell | all | persons_below_size_minimum | 334 | 2014 | 4.702e+04 | 0.04283 |
| batch_028 | kreis | 03158 | persons_all | 1 | 9.975 | 318 | 0.03137 |
| batch_032 | cell | all | size_partition | 341 | 369 | 1.346e+04 | 0.02741 |
| batch_028 | kreis | 03158 | persons_18plus | 1 | 7.477 | 281 | 0.02661 |
| batch_013 | cell | all | tenure_partition | 166 | 166 | 7106 | 0.02336 |
| batch_013 | cell | all | persons_without_households | 73 | 364 | 1.646e+04 | 0.02211 |

## Household composition

Synthetic households with the members of their seed household; adult = age >= 18. Definitions: `braunschweig.analysis.popsim_batch_diagnostics.composition`.

Census columns: observed census data (Zensus 2022 family-type table, 1 km); the household denominator is ALL private households of that table, the same universe as all synthetic households; census_persons_0_17_per_family_with_minor is an upper bound (minors outside families are in the numerator only); blind spots: households with two families; minors living without a parent; a lone parent living with a further adult counts as two adults in the model

| batch | households | households_with_minor | share_households_with_minor | one_adult_share_among_minor_households | persons_0_17_per_minor_household | share_persons_0_9_with_one_adult | share_persons_10_14_with_one_adult | minor_households_without_adult | census_family_households | census_families_with_minor | census_share_families_with_minor | census_lone_parent_share_among_families_with_minor | census_persons_0_17_per_family_with_minor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| batch_000 | 6664 | 726 | 0.1089 | 0.1818 | 1.525 | 0.1577 | 0.1681 | 2 | 6608 | 689 | 0.1043 | 0.2975 | 1.599 |
| batch_013 | 7106 | 1878 | 0.2643 | 0.1283 | 1.833 | 0.105 | 0.1365 | 0 | 7106 | 1740 | 0.2449 | 0.1902 | 1.973 |
| batch_028 | 26906 | 3872 | 0.1439 | 0.1278 | 1.624 | 0.1077 | 0.1665 | 0 | 2.691e+04 | 3831 | 0.1424 | 0.2313 | 1.641 |
| batch_032 | 13464 | 2423 | 0.18 | 0.1193 | 1.659 | 0.09852 | 0.1551 | 1 | 1.346e+04 | 2394 | 0.1778 | 0.2114 | 1.676 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| coverage_ratio | schulabschluss | 2.16 | 2 |
| coverage_ratio | beruflabschluss | 0.3552 | 0.5 |
