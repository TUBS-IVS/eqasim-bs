# PopulationSim batch diagnostics

- work directory: `/home/felix/i645/arm1p_A`
- batches: 2
- seed groups: 1 (de13d57636a9: 2 batch(es))
- repository commit: `a7e3ee34`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | de13d57636a9 | de13d57636a9 | 20171 | 2.181e+05 | 8608 | 0.03947 | 4427 | 57 | 0.07902 | 0.3331 | 0.8211 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| de13d57636a9 | <1 | 158006 | 0.2759 | 0.279 | 0.3076 |
| de13d57636a9 | 1-2 | 33521 | 0.2139 | 0.2134 | -0.04907 |
| de13d57636a9 | 2-3 | 11603 | 0.1296 | 0.127 | -0.2601 |
| de13d57636a9 | 3-5 | 8792 | 0.1535 | 0.1521 | -0.1344 |
| de13d57636a9 | 5-8 | 4027 | 0.1144 | 0.1159 | 0.1541 |
| de13d57636a9 | 8+ | 2148 | 0.1128 | 0.1126 | -0.01808 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| de13d57636a9 | age_sex | persons | True | 22 | 485709 | 880 | 0 | 0.002032 | 0.01593 | 7.837 |
| de13d57636a9 | employed_grid | persons | True | 10 | 230762 | 371 | 0 | 0.001893 | 0.00863 | 4.56 |
| de13d57636a9 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1281 | 2.194 |
| de13d57636a9 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.05257 | 0.5795 |
| de13d57636a9 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5197 | 0.9801 |
| de13d57636a9 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | household_type | households | True | 4 | 163844 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | tenure | households | True | 2 | 218097 | 5320 | 0 | 0.03504 | 0.003074 | 0.08772 |
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
| region_geography | ALL | ZENSUS100m |  | 1.507e+05 | 1.453e+05 | 3.281e+04 | -0.03582 | 0.2177 |
| region_geography | ALL | ZENSUS1km |  | 6.029e+04 | 6.051e+04 | 2362 | 0.003682 | 0.03918 |
| region_geography | ALL | KREIS |  | 4.062e+05 | 4.067e+05 | 5836 | 0.001385 | 0.01437 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 5 | 4 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| ZENSUS100m | 66 | 25 | 0 | 41 | 0 | 10 | 0 | 16 | 0 | 27 | 15 | 4 | 0 | 41 | 31 | 0.4697 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| rounding_shift | batch_000/M_AGE_18_19_agg_ZENSUS100m_target | 0.3495 | 0.1 |
| rounding_shift | batch_000/F_AGE_0_9_agg_ZENSUS100m_target | 0.1068 | 0.1 |
| rounding_shift | batch_000/F_AGE_10_14_agg_ZENSUS100m_target | 0.2434 | 0.1 |
| rounding_shift | batch_000/F_AGE_15_17_agg_ZENSUS100m_target | 0.378 | 0.1 |
| rounding_shift | batch_000/F_AGE_18_19_agg_ZENSUS100m_target | 0.3333 | 0.1 |
| rounding_shift | batch_000/5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.3038 | 0.1 |
| rounding_shift | batch_000/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.4681 | 0.1 |
| rounding_shift | batch_000/MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1326 | 0.1 |
| rounding_shift | batch_000/EMPLOYED_F_16_29_agg_ZENSUS100m_target | 0.11 | 0.1 |
| rounding_shift | batch_032/M_AGE_18_19_agg_ZENSUS100m_target | 0.1579 | 0.1 |
| rounding_shift | batch_032/F_AGE_15_17_agg_ZENSUS100m_target | 0.1131 | 0.1 |
| rounding_shift | batch_032/F_AGE_18_19_agg_ZENSUS100m_target | 0.25 | 0.1 |
| rounding_shift | batch_032/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.4065 | 0.1 |
| coverage_ratio | age_sex | 7.837 | 2 |
| coverage_ratio | employed_grid | 4.56 | 2 |
| coverage_ratio | schulabschluss | 2.194 | 2 |
| coverage_ratio | tenure | 0.08772 | 0.5 |
| coverage_ratio | building_type | 0 | 0.5 |
| universe_ratio | building_type/ZENSUS100m | 1.265 | 0.02 |
| sequential_fallback_rate | ZENSUS100m | 0.4697 | 0.05 |
