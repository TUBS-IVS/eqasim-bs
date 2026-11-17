# PopulationSim batch diagnostics

- work directory: `/home/felix/i645/arm1g_B`
- batches: 2
- seed groups: 1 (de13d57636a9: 2 batch(es))
- repository commit: `a7e3ee34`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | de13d57636a9 | de13d57636a9 | 20166 | 2.181e+05 | 8534 | 0.03913 | 4457 | 58 | 0.07637 | 0.3295 | 0.8248 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| de13d57636a9 | <1 | 158006 | 0.2759 | 0.3041 | 2.819 |
| de13d57636a9 | 1-2 | 33521 | 0.2139 | 0.2153 | 0.1397 |
| de13d57636a9 | 2-3 | 11603 | 0.1296 | 0.1245 | -0.5099 |
| de13d57636a9 | 3-5 | 8792 | 0.1535 | 0.1428 | -1.073 |
| de13d57636a9 | 5-8 | 4027 | 0.1144 | 0.1081 | -0.6315 |
| de13d57636a9 | 8+ | 2148 | 0.1128 | 0.1053 | -0.7442 |

## Control coverage

| seed_group | group | seed_table | exhaustive | n_categories | n_universe | n_uncovered | n_multi_hit | seed_weight_share | synthetic_share | ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| de13d57636a9 | age_sex | persons | True | 22 | 485709 | 880 | 0 | 0.002032 | 0.01693 | 8.329 |
| de13d57636a9 | employed_grid | persons | True | 10 | 230762 | 371 | 0 | 0.001893 | 0.008446 | 4.463 |
| de13d57636a9 | trip_class | persons | True | 4 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | employment_status | persons | True | 7 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | pt_ticket_group4 | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | leisure_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | escort_participation | persons | True | 2 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | work_by_employment | persons | True | 4 | 422056 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | education_band | persons | True | 6 | 485709 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.129 | 2.209 |
| de13d57636a9 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.05287 | 0.5828 |
| de13d57636a9 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5211 | 0.9827 |
| de13d57636a9 | household_size | households | True | 6 | 218097 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | household_type | households | True | 4 | 163844 | 0 | 0 | 0 | 0 |  |
| de13d57636a9 | tenure | households | True | 2 | 218097 | 5320 | 0 | 0.03504 | 0.003074 | 0.08774 |
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
| region_geography | ALL | ZENSUS100m |  | 1.507e+05 | 1.451e+05 | 3.203e+04 | -0.0372 | 0.2125 |
| region_geography | ALL | ZENSUS1km |  | 6.029e+04 | 6.05e+04 | 2573 | 0.003433 | 0.04268 |
| region_geography | ALL | KREIS |  | 4.062e+05 | 4.062e+05 | 5916 | 9.535e-05 | 0.01456 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 5 | 4 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 5 | 1 | 0.2 |
| ZENSUS100m | 66 | 26 | 0 | 40 | 0 | 13 | 0 | 13 | 0 | 22 | 14 | 5 | 0 | 49 | 27 | 0.4091 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| rounding_shift | batch_000/M_AGE_10_14_agg_ZENSUS100m_target | 0.1206 | 0.1 |
| rounding_shift | batch_000/M_AGE_18_19_agg_ZENSUS100m_target | 0.3786 | 0.1 |
| rounding_shift | batch_000/F_AGE_10_14_agg_ZENSUS100m_target | 0.2237 | 0.1 |
| rounding_shift | batch_000/F_AGE_15_17_agg_ZENSUS100m_target | 0.5244 | 0.1 |
| rounding_shift | batch_000/F_AGE_18_19_agg_ZENSUS100m_target | 0.5 | 0.1 |
| rounding_shift | batch_000/5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.3797 | 0.1 |
| rounding_shift | batch_000/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.5106 | 0.1 |
| rounding_shift | batch_000/Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1204 | 0.1 |
| rounding_shift | batch_000/Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1134 | 0.1 |
| rounding_shift | batch_000/MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1768 | 0.1 |
| rounding_shift | batch_000/EMPLOYED_M_50_59_agg_ZENSUS100m_target | 0.1085 | 0.1 |
| rounding_shift | batch_032/M_AGE_18_19_agg_ZENSUS100m_target | 0.2149 | 0.1 |
| rounding_shift | batch_032/F_AGE_15_17_agg_ZENSUS100m_target | 0.107 | 0.1 |
| rounding_shift | batch_032/F_AGE_18_19_agg_ZENSUS100m_target | 0.2277 | 0.1 |
| rounding_shift | batch_032/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.439 | 0.1 |
| coverage_ratio | age_sex | 8.329 | 2 |
| coverage_ratio | employed_grid | 4.463 | 2 |
| coverage_ratio | schulabschluss | 2.209 | 2 |
| coverage_ratio | tenure | 0.08774 | 0.5 |
| coverage_ratio | building_type | 0 | 0.5 |
| universe_ratio | building_type/ZENSUS100m | 1.265 | 0.02 |
| sequential_fallback_rate | ZENSUS1km | 0.2 | 0.05 |
| sequential_fallback_rate | ZENSUS100m | 0.4091 | 0.05 |
