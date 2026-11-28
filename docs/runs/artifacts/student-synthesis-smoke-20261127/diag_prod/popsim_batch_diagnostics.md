# PopulationSim batch diagnostics

- work directory: `<run server home>/popsim100_20261120/popsim_work`
- batches: 1
- seed groups: 1 (b69248d9b030: 1 batch(es))
- repository commit: `7c6edc8fd7d121ff497a5dc7e0d27e99624b0e37`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | b69248d9b030 | b69248d9b030 | 25649 | 2.181e+05 | 16914 | 0.07755 | 6180 | 69 | 0.07646 | 0.2719 | 0.5355 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| b69248d9b030 | <1 | 158006 | 0.2759 | 0.2764 | 0.04853 |
| b69248d9b030 | 1-2 | 33521 | 0.2139 | 0.213 | -0.08366 |
| b69248d9b030 | 2-3 | 11603 | 0.1296 | 0.1225 | -0.7027 |
| b69248d9b030 | 3-5 | 8792 | 0.1535 | 0.1508 | -0.2727 |
| b69248d9b030 | 5-8 | 4027 | 0.1144 | 0.1219 | 0.7508 |
| b69248d9b030 | 8+ | 2148 | 0.1128 | 0.1154 | 0.2597 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| b69248d9b030 | age_sex | persons | True | 22 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | employed_grid | persons | True | 10 | 230762 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1199 | 2.054 |
| b69248d9b030 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.0315 | 0.3473 |
| b69248d9b030 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.4603 | 0.868 |
| b69248d9b030 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | household_minor | households | False | 2 | 218097 | 169869 | 0 | 0.7773 | 0.8584 | 1.104 |
| b69248d9b030 | tenure | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | building_type | households | True | 3 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | own_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | own_bikes | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | economic_status | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | number_of_cars | households | True | 4 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | number_of_bicycles | households | True | 5 | 218097 | 0 | 0 | 0 | 0 |  |
| b69248d9b030 | has_ebike | households | True | 2 | 218097 | 0 | 0 | 0 | 0 |  |

## Resolved seed records by raw code

| seed_group | attribute | raw_codes | seed_table | n_records | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|
| b69248d9b030 | HP_SEX_raw | 3,9 | persons | 880 | 0.002032 | 0.00385 | 1.894 |
| b69248d9b030 | H_MIETE_raw | not 1,2 | households | 5320 | 0.03504 | 0.02944 | 0.8401 |
| b69248d9b030 | haustyp_raw | 95 | households | 8322 | 0.06185 | 0.05408 | 0.8743 |

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
| region_geography | ALL | ZENSUS100m |  | 1.714e+05 | 1.714e+05 | 9946 | -0.000175 | 0.05802 |
| region_geography | ALL | ZENSUS1km |  | 8.057e+04 | 8.058e+04 | 172 | 7.447e-05 | 0.002135 |
| region_geography | ALL | KREIS |  | 4.993e+05 | 4.993e+05 | 774.7 | -9.057e-05 | 0.001551 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 4 | 1 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS100m | 23 | 3 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Input consistency (20 largest shares)

`share` = contradiction `amount` / `base` of the check; Kreis rows compare the KREIS control table with the grid tables, cell rows are contradictions inside the 100 m cells (`braunschweig.popsim.input_consistency`); `ratio_reported` rows (survey ratio, no bound) are in `input_consistency.csv` only.

| batch | scope | key | check | cells | amount | base | share |
|---|---|---|---|---|---|---|---|
| batch_030 | kreis | 03157 | persons_0_17 | 1 | 1.68 | 6 | 0.28 |
| batch_030 | kreis | 03157 | persons_18plus | 1 | 2.672 | 24 | 0.1113 |
| batch_030 | kreis | 03157 | persons_14plus_low | 1 | 2.012 | 24 | 0.08383 |
| batch_030 | cell | all | persons_above_size_maximum | 275 | 2013 | 4.47e+04 | 0.04504 |
| batch_030 | cell | all | persons_below_size_minimum | 331 | 1963 | 4.47e+04 | 0.04392 |
| batch_030 | kreis | 03157 | persons_all | 1 | 0.9919 | 30 | 0.03306 |
| batch_030 | cell | all | size_partition | 309 | 340 | 2.565e+04 | 0.01326 |
| batch_030 | cell | all | building_type_partition | 173 | 176 | 2.565e+04 | 0.006862 |
| batch_030 | kreis | 03154 | persons_18plus | 1 | 8.093 | 1328 | 0.006094 |
| batch_030 | kreis | 03154 | persons_all | 1 | 9.53 | 1578 | 0.006039 |
| batch_030 | kreis | 03154 | persons_0_17 | 1 | 1.437 | 250 | 0.005749 |
| batch_030 | cell | all | tenure_partition | 131 | 131 | 2.565e+04 | 0.005107 |
| batch_030 | cell | all | households_without_persons | 11 | 35 | 2.565e+04 | 0.001365 |
| batch_030 | kreis | 03158 | persons_0_17 | 1 | 1.085 | 877 | 0.001238 |
| batch_030 | kreis | 03154 | households | 1 | 0.7193 | 717 | 0.001003 |
| batch_030 | kreis | 03158 | persons_all | 1 | 4.32 | 5122 | 0.0008435 |
| batch_030 | kreis | 03158 | persons_18plus | 1 | 3.279 | 4245 | 0.0007723 |
| batch_030 | cell | all | persons_without_households | 3 | 28 | 4.47e+04 | 0.0006265 |
| batch_030 | kreis | 03157 | households | 1 | 0.003037 | 5 | 0.0006075 |
| batch_030 | kreis | 03101 | persons_0_17 | 1 | 2.775 | 4729 | 0.0005868 |

## Household composition

Synthetic households with the members of their seed household; adult = age >= 18. Definitions: `braunschweig.analysis.popsim_batch_diagnostics.composition`.

| batch | households | households_with_minor | share_households_with_minor | one_adult_share_among_minor_households | persons_0_17_per_minor_household | share_persons_0_9_with_one_adult | share_persons_10_14_with_one_adult | minor_households_without_adult |
|---|---|---|---|---|---|---|---|---|
| batch_030 | 25649 | 3632 | 0.1416 | 0.2252 | 1.616 | 0.1897 | 0.2965 | 0 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| coverage_ratio | schulabschluss | 2.054 | 2 |
| coverage_ratio | beruflabschluss | 0.3473 | 0.5 |
