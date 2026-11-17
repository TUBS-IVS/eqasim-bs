# PopulationSim batch diagnostics

- work directory: `/home/felix/i647/arm2_A`
- batches: 2
- seed groups: 1 (0031c7e0b140: 2 batch(es))
- repository commit: `81e41c9b316295a22e4b4ee4004757aa650cc5e3`

## Reuse (region rows)

| scope | seed_group | subject | n_synthetic_households | n_seed_households | n_distinct_used | distinct_share_of_seed | kish_n_eff | max_copies | top100_share | top1000_share | top5000_share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| region | 0031c7e0b140 | 0031c7e0b140 | 20153 | 2.181e+05 | 8807 | 0.04038 | 4694 | 43 | 0.07443 | 0.3215 | 0.8111 |

## Weight classes

| seed_group | weight_class | n_seed | seed_weight_share | synthetic_share | difference_pp |
|---|---|---|---|---|---|
| 0031c7e0b140 | <1 | 158006 | 0.2759 | 0.2874 | 1.141 |
| 0031c7e0b140 | 1-2 | 33521 | 0.2139 | 0.2038 | -1.003 |
| 0031c7e0b140 | 2-3 | 11603 | 0.1296 | 0.1178 | -1.177 |
| 0031c7e0b140 | 3-5 | 8792 | 0.1535 | 0.146 | -0.751 |
| 0031c7e0b140 | 5-8 | 4027 | 0.1144 | 0.1235 | 0.9137 |
| 0031c7e0b140 | 8+ | 2148 | 0.1128 | 0.1215 | 0.8752 |

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
| 0031c7e0b140 | schulabschluss | persons | False | 3 | 417802 | 23185 | 0 | 0.05839 | 0.1217 | 2.084 |
| 0031c7e0b140 | beruflabschluss | persons | False | 3 | 417802 | 39986 | 0 | 0.09071 | 0.04736 | 0.5221 |
| 0031c7e0b140 | employed | persons | False | 1 | 485709 | 254894 | 0 | 0.5303 | 0.5182 | 0.9771 |
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
| 0031c7e0b140 | HP_SEX_raw | 3,9 | persons | 880 | 0.002032 | 0.005071 | 2.495 |
| 0031c7e0b140 | H_MIETE_raw | not 1,2 | households | 5320 | 0.03504 | 0.02828 | 0.8072 |
| 0031c7e0b140 | haustyp_raw | 95 | households | 8322 | 0.06185 | 0.06371 | 1.03 |

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
| region_geography | ALL | ZENSUS100m |  | 1.454e+05 | 1.459e+05 | 2.748e+04 | 0.003266 | 0.189 |
| region_geography | ALL | ZENSUS1km |  | 6.029e+04 | 6.046e+04 | 2420 | 0.002786 | 0.04014 |
| region_geography | ALL | KREIS |  | 4.062e+05 | 4.067e+05 | 5921 | 0.00144 | 0.01458 |

## Solver paths per level (summed over batches)

`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did not come from the simultaneous solution (sequential, single feasible sub zone, or all-smart-rounded).

| level | n_parents | first_optimal | first_feasible | first_infeasible | first_not_solved | retry_optimal | retry_feasible | retry_infeasible | retry_not_solved | sequential_fallback | no_infeasible_subzones | single_feasible_subzone | all_subzones_infeasible | infeasible_subzones | non_simultaneous | non_simultaneous_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KREIS | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ZENSUS1km | 5 | 4 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| ZENSUS100m | 66 | 28 | 0 | 38 | 0 | 14 | 0 | 14 | 0 | 20 | 10 | 4 | 0 | 42 | 24 | 0.3636 |

## Warnings

| check | subject | value | threshold |
|---|---|---|---|
| rounding_shift | batch_000/M_AGE_10_14_agg_ZENSUS100m_target | 0.1256 | 0.1 |
| rounding_shift | batch_000/M_AGE_15_17_agg_ZENSUS100m_target | 0.2344 | 0.1 |
| rounding_shift | batch_000/M_AGE_18_19_agg_ZENSUS100m_target | 0.4175 | 0.1 |
| rounding_shift | batch_000/F_AGE_15_17_agg_ZENSUS100m_target | 0.4268 | 0.1 |
| rounding_shift | batch_000/F_AGE_18_19_agg_ZENSUS100m_target | 0.4881 | 0.1 |
| rounding_shift | batch_000/5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.4304 | 0.1 |
| rounding_shift | batch_000/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.5106 | 0.1 |
| rounding_shift | batch_000/Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1229 | 0.1 |
| rounding_shift | batch_000/Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.1313 | 0.1 |
| rounding_shift | batch_000/MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target | 0.2376 | 0.1 |
| rounding_shift | batch_000/building_type_sonstiges_ZENSUS100m_target | 0.1294 | 0.1 |
| rounding_shift | batch_000/EMPLOYED_M_30_39_agg_ZENSUS100m_target | 0.1126 | 0.1 |
| rounding_shift | batch_032/M_AGE_18_19_agg_ZENSUS100m_target | 0.2105 | 0.1 |
| rounding_shift | batch_032/F_AGE_18_19_agg_ZENSUS100m_target | 0.1473 | 0.1 |
| rounding_shift | batch_032/6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target | 0.374 | 0.1 |
| coverage_ratio | schulabschluss | 2.084 | 2 |
| sequential_fallback_rate | ZENSUS100m | 0.3636 | 0.05 |
