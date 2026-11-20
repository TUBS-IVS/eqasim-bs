# PopulationSim batch diagnostics

- work directory: `/home/felix/i657/arm4/arm_4`
- batches: 2
- seed groups: 1 (0031c7e0b140: 2 batch(es))
- repository commit: `7b194d41849cdbd3b616c6ec57f00fb42b1f5669`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | 0031c7e0b140 | 0031c7e0b140 | 20107 | 2.181e+05 | 15986 | 0.0733 | 9432 | 55 | 0.05147 | 0.1876 | 0.4536 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| 0031c7e0b140 | <1 | 158006 | 0.2759 | 0.2952 | 1.928 |
| 0031c7e0b140 | 1-2 | 33521 | 0.2139 | 0.2156 | 0.173 |
| 0031c7e0b140 | 2-3 | 11603 | 0.1296 | 0.1234 | -0.6176 |
| 0031c7e0b140 | 3-5 | 8792 | 0.1535 | 0.1469 | -0.6579 |
| 0031c7e0b140 | 5-8 | 4027 | 0.1144 | 0.1115 | -0.2865 |
| 0031c7e0b140 | 8+ | 2148 | 0.1128 | 0.1074 | -0.5393 |

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
| 0031c7e0b140 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1256 | 2.15 |
| 0031c7e0b140 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.04796 | 0.5287 |
| 0031c7e0b140 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5214 | 0.9832 |
| 0031c7e0b140 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| 0031c7e0b140 | household_type | households | True | 4 | 163844 | 0 | 0 | 0 | 0 |  |
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
| 0031c7e0b140 | HP_SEX_raw | 3,9 | persons | 880 | 0.002032 | 0.006273 | 3.087 |
| 0031c7e0b140 | H_MIETE_raw | not 1,2 | households | 5320 | 0.03504 | 0.03208 | 0.9155 |
| 0031c7e0b140 | haustyp_raw | 95 | households | 8322 | 0.06185 | 0.06087 | 0.9842 |

## Control universe

| group | geography | target_sum | household_total | ratio |
|---|---|---|---|---|
| household_size | ZENSUS100m | 2.009e+04 | 2.009e+04 | 0.9998 |
| tenure | ZENSUS100m | 2.009e+04 | 2.009e+04 | 1 |
| building_type | ZENSUS100m | 2.01e+04 | 2.009e+04 | 1 |
| own_cars | ZENSUS1km | 2.01e+04 | 2.009e+04 | 1 |
| own_bikes | ZENSUS1km | 2.01e+04 | 2.009e+04 | 1 |
| economic_status | KREIS | 2.01e+04 | 2.009e+04 | 1 |
| number_of_cars | KREIS | 2.01e+04 | 2.009e+04 | 1 |
| number_of_bicycles | KREIS | 2.01e+04 | 2.009e+04 | 1 |
| has_ebike | KREIS | 2.01e+04 | 2.009e+04 | 1 |

## Control fit per geography (region)

| scope | control | geography | importance | target_sum | synthetic_sum | abs_error | bias | wape |
|---|---|---|---|---|---|---|---|---|
| region_geography | ALL | ZENSUS100m |  | 1.454e+05 | 1.455e+05 | 2.207e+04 | 0.0004676 | 0.1517 |
| region_geography | ALL | ZENSUS1km |  | 6.029e+04 | 6.032e+04 | 2086 | 0.0004976 | 0.0346 |
| region_geography | ALL | KREIS |  | 4.062e+05 | 4.065e+05 | 3185 | 0.0008931 | 0.007842 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 5 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS100m | 66 | 9 | 57 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Household conservation per level (summed over batches)

`abs_deviation_share` = sum over (household group, parent zone) pairs of `|sub integer weight sum - parent integer weight|`, divided by the parent weight sum; 0 means every sub level adds up to its parent.

| level | parent_level | n_pairs | parent_weight_sum | sub_weight_sum | abs_deviation_sum | abs_deviation_share |
|---|---|---|---|---|---|---|
| KREIS | STAAT | 17670 | 2.009e+04 | 2.009e+04 | 0 | 0 |
| ZENSUS1km | KREIS | 17857 | 2.009e+04 | 2.009e+04 | 0 | 0 |
| ZENSUS100m | ZENSUS1km | 19685 | 2.009e+04 | 2.011e+04 | 116 | 0.005773 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| coverage_ratio | schulabschluss | 2.15 | 2 |
