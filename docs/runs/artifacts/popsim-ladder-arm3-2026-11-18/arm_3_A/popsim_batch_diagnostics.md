# PopulationSim batch diagnostics

- work directory: `/home/felix/i646/ev/controlled_off`
- batches: 2
- seed groups: 1 (0031c7e0b140: 2 batch(es))
- repository commit: `f796f625a90f4e117a2984955fa8c23a05226d79`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | 0031c7e0b140 | 0031c7e0b140 | 20154 | 2.181e+05 | 15858 | 0.07271 | 9254 | 55 | 0.0522 | 0.193 | 0.4612 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| 0031c7e0b140 | <1 | 158006 | 0.2759 | 0.2986 | 2.261 |
| 0031c7e0b140 | 1-2 | 33521 | 0.2139 | 0.214 | 0.01359 |
| 0031c7e0b140 | 2-3 | 11603 | 0.1296 | 0.1226 | -0.7009 |
| 0031c7e0b140 | 3-5 | 8792 | 0.1535 | 0.1486 | -0.4937 |
| 0031c7e0b140 | 5-8 | 4027 | 0.1144 | 0.1109 | -0.3472 |
| 0031c7e0b140 | 8+ | 2148 | 0.1128 | 0.1054 | -0.733 |

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
| 0031c7e0b140 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1252 | 2.144 |
| 0031c7e0b140 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.04785 | 0.5275 |
| 0031c7e0b140 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5188 | 0.9783 |
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
| 0031c7e0b140 | HP_SEX_raw | 3,9 | persons | 880 | 0.002032 | 0.006349 | 3.124 |
| 0031c7e0b140 | H_MIETE_raw | not 1,2 | households | 5320 | 0.03504 | 0.02952 | 0.8425 |
| 0031c7e0b140 | haustyp_raw | 95 | households | 8322 | 0.06185 | 0.06396 | 1.034 |

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
| region_geography | ALL | ZENSUS100m |  | 1.454e+05 | 1.458e+05 | 2.336e+04 | 0.002696 | 0.1606 |
| region_geography | ALL | ZENSUS1km |  | 6.029e+04 | 6.046e+04 | 1985 | 0.002836 | 0.03292 |
| region_geography | ALL | KREIS |  | 4.062e+05 | 4.071e+05 | 4074 | 0.002385 | 0.01003 |

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
| KREIS | STAAT | 17596 | 2.009e+04 | 2.009e+04 | 0 | 0 |
| ZENSUS1km | KREIS | 17804 | 2.009e+04 | 2.009e+04 | 0 | 0 |
| ZENSUS100m | ZENSUS1km | 19676 | 2.009e+04 | 2.015e+04 | 331 | 0.01647 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| rounding_shift | batch_000/building_type_sonstiges_ZENSUS100m_target | 0.1494 | 0.1 |
| coverage_ratio | schulabschluss | 2.144 | 2 |
