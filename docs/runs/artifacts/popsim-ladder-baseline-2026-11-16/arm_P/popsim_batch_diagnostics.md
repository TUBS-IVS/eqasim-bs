# PopulationSim batch diagnostics

- work directory: `/home/felix/prod100_20261031/popsim_work`
- batches: 2
- seed groups: 1 (de13d57636a9: 2 batch(es))
- repository commit: `b1e4a5a53a08cc253528224c2061b4b4361d8588`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | de13d57636a9 | de13d57636a9 | 20188 | 2.181e+05 | 4395 | 0.02015 | 1178 | 129 | 0.2037 | 0.6449 | 1 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| de13d57636a9 | <1 | 158006 | 0.2759 | 0.006291 | -26.96 |
| de13d57636a9 | 1-2 | 33521 | 0.2139 | 0.06449 | -14.94 |
| de13d57636a9 | 2-3 | 11603 | 0.1296 | 0.1055 | -2.406 |
| de13d57636a9 | 3-5 | 8792 | 0.1535 | 0.2212 | 6.768 |
| de13d57636a9 | 5-8 | 4027 | 0.1144 | 0.2357 | 12.13 |
| de13d57636a9 | 8+ | 2148 | 0.1128 | 0.3669 | 25.41 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| de13d57636a9 | age_sex | persons | True | 22 | 485709 | 880 | 0 | 0.002032 | 0.0164 | 8.07 |
| de13d57636a9 | employed_grid | persons | True | 10 | 230762 | 371 | 0 | 0.001893 | 0.01054 | 5.571 |
| de13d57636a9 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1309 | 2.241 |
| de13d57636a9 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.05353 | 0.5901 |
| de13d57636a9 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5244 | 0.9889 |
| de13d57636a9 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | household_type | households | True | 4 | 163844 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | tenure | households | True | 2 | 218097 | 5320 | 0 | 0.03504 | 0.003814 | 0.1089 |
| de13d57636a9 | building_type | households | True | 3 | 218097 | 8322 | 0 | 0.06185 | 0 | 0 |
| de13d57636a9 | own_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | own_bikes | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | economic_status | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | number_of_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | number_of_bicycles | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | has_ebike | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |

## Control universe

| group | geography | target_sum | household_total | ratio |
|---|---|---|---|---|
| household_size | ZENSUS100m | 2.009e+04 | 2.009e+04 | 0.9998 |
| tenure | ZENSUS100m | 2.009e+04 | 2.009e+04 | 1 |
| building_type | ZENSUS100m | 2.541e+04 | 2.009e+04 | 1.265 |
| own_cars | ZENSUS1km | 2.01e+04 | 2.009e+04 | 1 |
| own_bikes | ZENSUS1km | 2.01e+04 | 2.009e+04 | 1 |
| economic_status | KREIS | 2.01e+04 | 2.009e+04 | 1 |
| number_of_cars | KREIS | 2.01e+04 | 2.009e+04 | 1 |
| number_of_bicycles | KREIS | 2.01e+04 | 2.009e+04 | 1 |
| has_ebike | KREIS | 2.01e+04 | 2.009e+04 | 1 |

## Control fit per geography (region)

| scope | control | geography | importance | target_sum | synthetic_sum | abs_error | bias | wape |
|---|---|---|---|---|---|---|---|---|
| region_geography | ALL | ZENSUS100m |  | 1.507e+05 | 1.455e+05 | 3.347e+04 | -0.03487 | 0.222 |
| region_geography | ALL | ZENSUS1km |  | 6.029e+04 | 6.056e+04 | 2461 | 0.004528 | 0.04082 |
| region_geography | ALL | KREIS |  | 4.062e+05 | 4.08e+05 | 5705 | 0.00449 | 0.01405 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 5 | 4 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| ZENSUS100m | 66 | 30 | 0 | 35 | 1 | 10 | 0 | 13 | 0 | 22 | 12 | 3 | 1 | 42 | 26 | 0.3939 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| weight_class_gap | <1 | -26.96 | 5 |
| weight_class_gap | 1-2 | -14.94 | 5 |
| weight_class_gap | 3-5 | 6.768 | 5 |
| weight_class_gap | 5-8 | 12.13 | 5 |
| weight_class_gap | 8+ | 25.41 | 5 |
| coverage_ratio | age_sex | 8.07 | 2 |
| coverage_ratio | employed_grid | 5.571 | 2 |
| coverage_ratio | schulabschluss | 2.241 | 2 |
| coverage_ratio | tenure | 0.1089 | 0.5 |
| coverage_ratio | building_type | 0 | 0.5 |
| universe_ratio | building_type/ZENSUS100m | 1.265 | 0.02 |
| sequential_fallback_rate | ZENSUS100m | 0.3939 | 0.05 |
