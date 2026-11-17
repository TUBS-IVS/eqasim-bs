# PopulationSim batch diagnostics

- work directory: `/home/felix/i645/arm1g_A`
- batches: 2
- seed groups: 1 (de13d57636a9: 2 batch(es))
- repository commit: `a7e3ee34`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | de13d57636a9 | de13d57636a9 | 20174 | 2.181e+05 | 8531 | 0.03912 | 4333 | 43 | 0.08382 | 0.3374 | 0.825 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| de13d57636a9 | <1 | 158006 | 0.2759 | 0.283 | 0.7049 |
| de13d57636a9 | 1-2 | 33521 | 0.2139 | 0.2112 | -0.2654 |
| de13d57636a9 | 2-3 | 11603 | 0.1296 | 0.1224 | -0.7131 |
| de13d57636a9 | 3-5 | 8792 | 0.1535 | 0.1576 | 0.4086 |
| de13d57636a9 | 5-8 | 4027 | 0.1144 | 0.1168 | 0.2416 |
| de13d57636a9 | 8+ | 2148 | 0.1128 | 0.109 | -0.3766 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| de13d57636a9 | age_sex | persons | True | 22 | 485709 | 880 | 0 | 0.002032 | 0.01576 | 7.756 |
| de13d57636a9 | employed_grid | persons | True | 10 | 230762 | 371 | 0 | 0.001893 | 0.007327 | 3.871 |
| de13d57636a9 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1304 | 2.234 |
| de13d57636a9 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.05343 | 0.589 |
| de13d57636a9 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5224 | 0.985 |
| de13d57636a9 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | household_type | households | True | 4 | 163844 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | tenure | households | True | 2 | 218097 | 5320 | 0 | 0.03504 | 0.00347 | 0.09902 |
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
| region_geography | ALL | ZENSUS100m |  | 1.507e+05 | 1.453e+05 | 3.276e+04 | -0.03623 | 0.2173 |
| region_geography | ALL | ZENSUS1km |  | 6.029e+04 | 6.052e+04 | 2447 | 0.003831 | 0.04059 |
| region_geography | ALL | KREIS |  | 4.062e+05 | 4.066e+05 | 5685 | 0.00112 | 0.014 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 5 | 4 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| ZENSUS100m | 66 | 29 | 0 | 37 | 0 | 12 | 0 | 13 | 0 | 23 | 12 | 2 | 0 | 46 | 25 | 0.3788 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| rounding_shift | batch_000/M_AGE_15_17_agg_ZENSUS100m_target | 0.1563 | 0.1 |
| rounding_shift | batch_000/M_AGE_18_19_agg_ZENSUS100m_target | 0.2913 | 0.1 |
| rounding_shift | batch_000/F_AGE_10_14_agg_ZENSUS100m_target | 0.1711 | 0.1 |
| rounding_shift | batch_000/F_AGE_15_17_agg_ZENSUS100m_target | 0.3415 | 0.1 |
| rounding_shift | batch_000/F_AGE_18_19_agg_ZENSUS100m_target | 0.4405 | 0.1 |
| rounding_shift | batch_000/F_AGE_60_69_agg_ZENSUS100m_target | 0.1012 | 0.1 |
| rounding_shift | batch_000/5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.3544 | 0.1 |
| rounding_shift | batch_000/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.4681 | 0.1 |
| rounding_shift | batch_000/Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1172 | 0.1 |
| rounding_shift | batch_000/MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1823 | 0.1 |
| rounding_shift | batch_000/EMPLOYED_F_16_29_agg_ZENSUS100m_target | 0.112 | 0.1 |
| rounding_shift | batch_032/M_AGE_18_19_agg_ZENSUS100m_target | 0.1974 | 0.1 |
| rounding_shift | batch_032/F_AGE_18_19_agg_ZENSUS100m_target | 0.2321 | 0.1 |
| rounding_shift | batch_032/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.439 | 0.1 |
| coverage_ratio | age_sex | 7.756 | 2 |
| coverage_ratio | employed_grid | 3.871 | 2 |
| coverage_ratio | schulabschluss | 2.234 | 2 |
| coverage_ratio | tenure | 0.09902 | 0.5 |
| coverage_ratio | building_type | 0 | 0.5 |
| universe_ratio | building_type/ZENSUS100m | 1.265 | 0.02 |
| sequential_fallback_rate | ZENSUS100m | 0.3788 | 0.05 |
