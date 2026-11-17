# PopulationSim batch diagnostics

- work directory: `/home/felix/prod100_20261031/popsim_work`
- batches: 60
- seed groups: 1 (de13d57636a9: 60 batch(es))
- repository commit: `b1e4a5a53a08cc253528224c2061b4b4361d8588`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | de13d57636a9 | de13d57636a9 | 558274 | 2.181e+05 | 15248 | 0.06991 | 1293 | 3796 | 0.2066 | 0.5702 | 0.8906 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| de13d57636a9 | <1 | 158006 | 0.2759 | 0.006228 | -26.97 |
| de13d57636a9 | 1-2 | 33521 | 0.2139 | 0.07753 | -13.63 |
| de13d57636a9 | 2-3 | 11603 | 0.1296 | 0.1047 | -2.486 |
| de13d57636a9 | 3-5 | 8792 | 0.1535 | 0.2196 | 6.612 |
| de13d57636a9 | 5-8 | 4027 | 0.1144 | 0.2379 | 12.36 |
| de13d57636a9 | 8+ | 2148 | 0.1128 | 0.354 | 24.12 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| de13d57636a9 | age_sex | persons | True | 22 | 485709 | 880 | 0 | 0.002032 | 0.01465 | 7.21 |
| de13d57636a9 | employed_grid | persons | True | 10 | 230762 | 371 | 0 | 0.001893 | 0.008133 | 4.297 |
| de13d57636a9 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1336 | 2.288 |
| de13d57636a9 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.04784 | 0.5274 |
| de13d57636a9 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5306 | 1.001 |
| de13d57636a9 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | household_type | households | True | 4 | 163844 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | tenure | households | True | 2 | 218097 | 5320 | 0 | 0.03504 | 0.003493 | 0.09968 |
| de13d57636a9 | building_type | households | True | 3 | 218097 | 8322 | 0 | 0.06185 | 3.941e-05 | 0.0006371 |
| de13d57636a9 | own_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | own_bikes | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | economic_status | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | number_of_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | number_of_bicycles | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | has_ebike | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |

## Control universe

| group | geography | target_sum | household_total | ratio |
|---|---|---|---|---|
| household_size | ZENSUS100m | 5.58e+05 | 5.582e+05 | 0.9997 |
| tenure | ZENSUS100m | 5.582e+05 | 5.582e+05 | 1 |
| building_type | ZENSUS100m | 6.098e+05 | 5.582e+05 | 1.093 |
| own_cars | ZENSUS1km | 5.581e+05 | 5.582e+05 | 0.9999 |
| own_bikes | ZENSUS1km | 5.583e+05 | 5.582e+05 | 1 |
| economic_status | KREIS | 5.582e+05 | 5.582e+05 | 1 |
| number_of_cars | KREIS | 5.582e+05 | 5.582e+05 | 1 |
| number_of_bicycles | KREIS | 5.582e+05 | 5.582e+05 | 1 |
| has_ebike | KREIS | 5.582e+05 | 5.582e+05 | 1 |

## Control fit per geography (region)

| scope | control | geography | importance | target_sum | synthetic_sum | abs_error | bias | wape |
|---|---|---|---|---|---|---|---|---|
| region_geography | ALL | ZENSUS100m |  | 4.273e+06 | 4.2e+06 | 8.438e+05 | -0.01719 | 0.1975 |
| region_geography | ALL | ZENSUS1km |  | 1.675e+06 | 1.675e+06 | 6.667e+04 | 0.0001851 | 0.03981 |
| region_geography | ALL | KREIS |  | 1.205e+07 | 1.206e+07 | 1.643e+05 | 0.0006276 | 0.01363 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 60 | 60 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 172 | 123 | 0 | 38 | 11 | 23 | 0 | 2 | 7 | 26 | 17 | 0 | 0 | 44 | 26 | 0.1512 |
| ZENSUS100m | 2233 | 911 | 0 | 1299 | 23 | 566 | 0 | 403 | 11 | 631 | 331 | 114 | 11 | 1675 | 756 | 0.3386 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| weight_class_gap | <1 | -26.97 | 5 |
| weight_class_gap | 1-2 | -13.63 | 5 |
| weight_class_gap | 3-5 | 6.612 | 5 |
| weight_class_gap | 5-8 | 12.36 | 5 |
| weight_class_gap | 8+ | 24.12 | 5 |
| coverage_ratio | age_sex | 7.21 | 2 |
| coverage_ratio | employed_grid | 4.297 | 2 |
| coverage_ratio | schulabschluss | 2.288 | 2 |
| coverage_ratio | tenure | 0.09968 | 0.5 |
| coverage_ratio | building_type | 0.0006371 | 0.5 |
| universe_ratio | building_type/ZENSUS100m | 1.093 | 0.02 |
| sequential_fallback_rate | ZENSUS1km | 0.1512 | 0.05 |
| sequential_fallback_rate | ZENSUS100m | 0.3386 | 0.05 |
